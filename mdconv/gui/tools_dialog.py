"""Subtela "Ferramentas": os conversores que não fazem parte do fluxo MIDI + SF2.

Abas:
1. **Música VGM** — conversão direta de VGM com AY-3-8910 ou YM2203, com a escolha do destino de
   cada canal do AY (automático, PSG 1–3, FM 1–6 ou omitir).
2. **Efeitos de chip** — VGM curto de AY → PSG/FM + código C, com os canais livres do jogo.
3. **Efeitos e vozes (WAV)** — PCM de 8 bits na taxa do driver.
4. **Banco .tfi** — monta o banco de patches prontos.

O trabalho roda na thread da janela principal (``MainWindow._run``): o andamento aparece no
registro de lá, e só uma tarefa roda por vez.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from mdconv.ay.convert import AUTO, CH_NAMES, check_routing, dest_label

# destinos oferecidos para cada canal do AY: (texto, valor)
DESTS = ([("Automático", AUTO)] + [(f"PSG tom {n + 1}", ("psg", n)) for n in range(3)]
         + [(f"FM {n + 1}", ("fm", n)) for n in range(6)] + [("Omitir (não tocar)", None)])
AY_KEYS = [(c, ch) for c in (0, 1) for ch in range(3)]


def _dest_to_str(d) -> str:
    return "auto" if d == AUTO else "off" if d is None else f"{d[0]}{d[1]}"


def _str_to_dest(s: str):
    if s == "off":
        return None
    if s and s[:-1] in ("psg", "fm") and s[-1].isdigit():
        return (s[:-1], int(s[-1]))
    return AUTO


class _FileList(QWidget):
    """Lista de arquivos com Adicionar / Remover / Limpar."""

    def __init__(self, owner, key: str, caption: str, filt: str):
        super().__init__()
        self.owner, self.key, self.caption, self.filt = owner, key, caption, filt
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(90)
        v.addWidget(self.list, 1)
        h = QHBoxLayout()
        for text, slot in (("Adicionar…", self.add), ("Remover", self.remove), ("Limpar", self.list.clear)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)

    def add(self):
        qs = self.owner.qsettings
        files, _ = QFileDialog.getOpenFileNames(self, self.caption, qs.value(self.key, str(Path.home())),
                                                self.filt + ";;Todos (*.*)")
        if not files:
            return
        qs.setValue(self.key, str(Path(files[0]).parent))
        have = set(self.files())
        for f in files:
            if f not in have:
                self.list.addItem(f)

    def remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def files(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]


class _DirRow(QWidget):
    def __init__(self, owner, key: str, caption: str, default: str = ""):
        super().__init__()
        self.owner, self.key, self.caption = owner, key, caption
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(owner.qsettings.value(key, default) or default)
        b = QPushButton("Procurar…")
        b.clicked.connect(self.browse)
        h.addWidget(self.edit, 1)
        h.addWidget(b)

    def browse(self):
        p = QFileDialog.getExistingDirectory(self, self.caption, self.edit.text() or str(Path.home()))
        if p:
            self.edit.setText(p)

    def path(self) -> str:
        p = self.edit.text().strip()
        if p:
            self.owner.qsettings.setValue(self.key, p)
        return p


class ToolsDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.qsettings = main.qsettings
        self.setWindowTitle("Ferramentas — conversores e efeitos")
        self.resize(760, 640)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_vgm(), "Música VGM (AY / YM2203)")
        self.tabs.addTab(self._build_chip_sfx(), "Efeitos de chip (AY)")
        self.tabs.addTab(self._build_wav(), "Efeitos e vozes (WAV)")
        self.tabs.addTab(self._build_tfi(), "Banco de patches .tfi")
        self.tabs.setCurrentIndex(int(self.qsettings.value("tools/tab", 0) or 0))
        self.tabs.currentChanged.connect(lambda i: self.qsettings.setValue("tools/tab", i))
        v.addWidget(self.tabs, 1)
        note = QLabel("O andamento e os relatórios aparecem no registro da janela principal.")
        note.setStyleSheet("color: gray")
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.close)
        v.addWidget(bb)

    # ------------------------------------------------------------- utilidades
    def _out_default(self) -> str:
        return self.main.ed_out.text() or str(Path.home() / "mdconv" / "saida")

    def _need(self, files, dst) -> bool:
        if not files:
            QMessageBox.information(self, self.windowTitle(), "Adicione ao menos um arquivo à lista.")
            return False
        if not dst:
            QMessageBox.information(self, self.windowTitle(), "Escolha a pasta de saída.")
            return False
        return True

    @staticmethod
    def _checks(label_fmt: str, n: int, on: tuple) -> tuple[QWidget, list[QCheckBox]]:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        cks = []
        for i in range(n):
            ck = QCheckBox(label_fmt.format(i + 1))
            ck.setChecked(i in on)
            cks.append(ck)
            h.addWidget(ck)
        h.addStretch(1)
        return w, cks

    def _load_checks(self, key: str, cks, default: tuple):
        raw = self.qsettings.value(key, None)
        on = default if raw in (None, "") else tuple(int(x) for x in str(raw).split(",") if x != "")
        for i, ck in enumerate(cks):
            ck.setChecked(i in on)

    # ------------------------------------------------------------- 1. música VGM
    def _build_vgm(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("Arquivos VGM / VGZ (AY-3-8910 ou YM2203)")
        gv = QVBoxLayout(g)
        self.vgm_files = _FileList(self, "ay/dir", "VGM com AY-3-8910 ou YM2203", "VGM (*.vgm *.vgz)")
        gv.addWidget(self.vgm_files)
        v.addWidget(g, 1)

        f = QFormLayout()
        self.vgm_out = _DirRow(self, "tools/vgm_out", "Pasta onde gravar XGM, VGM e prévias", self._out_default())
        f.addRow("Pasta de saída:", self.vgm_out)
        self.vgm_grid = QCheckBox("Travar o andamento na grade de 60 Hz (muda o andamento em até 6%)")
        self.vgm_grid.setToolTip("Ajusta o andamento para o passo rítmico durar um número inteiro de quadros\n"
                                 "(ritmo sem oscilação no XGM). Desmarcado = andamento original.")
        self.vgm_grid.setChecked(self.qsettings.value("tools/vgm_grid", "false") == "true")
        self.vgm_trim = QCheckBox("Começar na primeira nota (tira o silêncio do início)")
        self.vgm_trim.setChecked(self.qsettings.value("tools/vgm_trim", "true") != "false")
        f.addRow(self.vgm_grid)
        f.addRow(self.vgm_trim)
        v.addLayout(f)

        g = QGroupBox("Destino de cada canal do AY-3-8910")
        gv = QVBoxLayout(g)
        tip = QLabel("Vale para todos os arquivos da lista (no YM2203, só o SSG passa por aqui e segue o "
                     "automático). \"Automático\": tom que cabe no PSG fica no PSG; parte grave (abaixo de "
                     "~109 Hz) ou do 2º AY vai para o FM como onda quadrada.")
        tip.setWordWrap(True)
        gv.addWidget(tip)
        self.route_table = QTableWidget(len(AY_KEYS), 3)
        self.route_table.setHorizontalHeaderLabels(["Canal do AY", "Destino", "No automático (1º arquivo)"])
        self.route_table.verticalHeader().setVisible(False)
        self.route_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.route_table.setSelectionMode(QAbstractItemView.NoSelection)
        hh = self.route_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.route_combos = {}
        for row, key in enumerate(AY_KEYS):
            c, ch = key
            self.route_table.setItem(row, 0, QTableWidgetItem(f"AY{c + 1} — canal {CH_NAMES[ch]}"))
            cb = QComboBox()
            for text, val in DESTS:
                cb.addItem(text, _dest_to_str(val))
            saved = self.qsettings.value(f"tools/route_{c}{ch}", "auto")
            cb.setCurrentIndex(max(0, cb.findData(saved)))
            self.route_combos[key] = cb
            self.route_table.setCellWidget(row, 1, cb)
            self.route_table.setItem(row, 2, QTableWidgetItem("—"))
        self.route_table.setMinimumHeight(self.route_table.verticalHeader().length() + 30)
        gv.addWidget(self.route_table)
        h = QHBoxLayout()
        b_an = QPushButton("Analisar o 1º arquivo")
        b_an.setToolTip("Mostra quais canais tocam no 1º arquivo da lista e para onde o automático os mandaria.")
        b_an.clicked.connect(self.analyze_first)
        b_auto = QPushButton("Tudo automático")
        b_auto.clicked.connect(lambda: [cb.setCurrentIndex(0) for cb in self.route_combos.values()])
        h.addWidget(b_an)
        h.addWidget(b_auto)
        h.addStretch(1)
        gv.addLayout(h)
        v.addWidget(g)

        b = QPushButton("Converter")
        b.setDefault(True)
        b.clicked.connect(self.run_vgm)
        v.addWidget(b, 0, Qt.AlignRight)
        return w

    def routing(self) -> dict:
        r = {}
        for (c, ch), cb in self.route_combos.items():
            self.qsettings.setValue(f"tools/route_{c}{ch}", cb.currentData())
            d = _str_to_dest(cb.currentData())
            if d != AUTO:
                r[(c, ch)] = d
        return r

    def analyze_first(self):
        files = self.vgm_files.files()
        if not files:
            QMessageBox.information(self, self.windowTitle(), "Adicione ao menos um arquivo à lista.")
            return
        path = files[0]
        grid, trim = self.vgm_grid.isChecked(), self.vgm_trim.isChecked()

        def work(progress):
            from mdconv.vgmconv import detect_chip
            chip = detect_chip(path)
            if chip != "AY-3-8910":
                return chip, None, []
            from mdconv.ay.convert import convert, read_vgm
            conv = convert(read_vgm(path), grid=grid, trim=trim)
            return chip, conv.routes, conv.report

        def done(result):
            chip, routes, report = result
            for row, key in enumerate(AY_KEYS):
                if routes is None:
                    txt = f"({chip}: não usa esta tabela)"
                elif key not in routes:
                    txt = "não toca"
                else:
                    txt = dest_label(routes[key])
                self.route_table.item(row, 2).setText(txt)
            for line in report:
                self.main._log("  " + line)

        self.main._run(f"Analisando {Path(path).name}", work, done)

    def run_vgm(self):
        files, dst = self.vgm_files.files(), self.vgm_out.path()
        if not self._need(files, dst):
            return
        routing = self.routing()
        errs = check_routing(routing)
        if errs:
            QMessageBox.warning(self, self.windowTitle(), "Destinos repetidos:\n\n" + "\n".join(errs))
            return
        self.qsettings.setValue("tools/vgm_grid", "true" if self.vgm_grid.isChecked() else "false")
        self.qsettings.setValue("tools/vgm_trim", "true" if self.vgm_trim.isChecked() else "false")
        if routing:
            self.main._log("Rotas escolhidas: " + ", ".join(
                f"AY{c + 1} {CH_NAMES[ch]} → {dest_label(d)}" for (c, ch), d in sorted(routing.items())))
        self.main.convert_vgm_job(files, dst, grid=self.vgm_grid.isChecked(), trim=self.vgm_trim.isChecked(),
                                  routing=routing or None)

    # ------------------------------------------------------------- 2. efeitos de chip
    def _build_chip_sfx(self) -> QWidget:
        from mdconv import chipsfx
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("Efeitos em VGM de AY-3-8910 (um efeito por arquivo)")
        gv = QVBoxLayout(g)
        self.sfx_files = _FileList(self, "chipsfx/dir", "VGM de AY-3-8910 com o efeito", "VGM (*.vgm *.vgz)")
        gv.addWidget(self.sfx_files)
        v.addWidget(g, 1)
        f = QFormLayout()
        self.sfx_out = _DirRow(self, "tools/chipsfx_out", "Pasta onde gravar os efeitos", self._out_default())
        f.addRow("Pasta de saída:", self.sfx_out)
        row, self.sfx_psg = self._checks("Tom {}", 3, chipsfx.DEFAULT_PSG)
        self._load_checks("tools/chipsfx_psg", self.sfx_psg, chipsfx.DEFAULT_PSG)
        f.addRow("Canais PSG livres:", row)
        row, self.sfx_fm = self._checks("FM {}", 6, chipsfx.DEFAULT_FM)
        self._load_checks("tools/chipsfx_fm", self.sfx_fm, chipsfx.DEFAULT_FM)
        f.addRow("Canais FM livres:", row)
        v.addLayout(f)
        tip = QLabel("Marque os canais que a música do jogo deixa livres para efeitos. Os tons do efeito "
                     "ocupam os canais PSG marcados, em ordem; tons graves demais para o PSG vão para os FM "
                     "marcados, como onda quadrada. O FM 6 costuma estar com o PCM do XGM. "
                     "Sai .vgm, prévia .wav e um .c para o SGDK com nome_play() e nome_update().")
        tip.setWordWrap(True)
        v.addWidget(tip)
        b = QPushButton("Converter")
        b.clicked.connect(self.run_chip_sfx)
        v.addWidget(b, 0, Qt.AlignRight)
        return w

    def run_chip_sfx(self):
        files, dst = self.sfx_files.files(), self.sfx_out.path()
        if not self._need(files, dst):
            return
        psg = [i for i, ck in enumerate(self.sfx_psg) if ck.isChecked()]
        fm = [i for i, ck in enumerate(self.sfx_fm) if ck.isChecked()]
        if not psg and not fm:
            QMessageBox.information(self, self.windowTitle(), "Marque ao menos um canal livre (PSG ou FM).")
            return
        self.qsettings.setValue("tools/chipsfx_psg", ",".join(map(str, psg)))
        self.qsettings.setValue("tools/chipsfx_fm", ",".join(map(str, fm)))
        self.main.chip_sfx_files_job(files, dst, psg, fm)

    # ------------------------------------------------------------- 3. WAV
    def _build_wav(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("WAVs de efeitos sonoros e vozes")
        gv = QVBoxLayout(g)
        self.wav_files = _FileList(self, "sfx/dir", "WAVs de efeitos e vozes", "WAV (*.wav)")
        gv.addWidget(self.wav_files)
        v.addWidget(g, 1)
        f = QFormLayout()
        self.wav_out = _DirRow(self, "tools/wav_out", "Pasta onde gravar os WAVs prontos para o SGDK",
                               self._out_default())
        f.addRow("Pasta de saída:", self.wav_out)
        self.wav_rate = QSpinBox()
        self.wav_rate.setRange(4000, 32000)
        self.wav_rate.setSingleStep(50)
        self.wav_rate.setSuffix(" Hz")
        self.wav_rate.setValue(int(self.qsettings.value("tools/wav_rate", 14000) or 14000))
        self.wav_rate.setToolTip("14000 Hz = XGM v1.")
        f.addRow("Taxa do driver:", self.wav_rate)
        self.wav_comp = QCheckBox("Comprimir levemente a dinâmica (menos chiado nos finais; recomendado)")
        self.wav_comp.setChecked(self.qsettings.value("tools/wav_comp", "true") != "false")
        self.wav_same = QCheckBox("Manter o volume relativo entre os arquivos (mesmo ganho para todos)")
        self.wav_same.setToolTip("Marcado: um passo continua mais baixo que uma explosão (os sons fracos ficam "
                                 "com um pouco mais de chiado).\nDesmarcado: cada som no volume máximo.")
        self.wav_same.setChecked(self.qsettings.value("tools/wav_same", "false") == "true")
        f.addRow(self.wav_comp)
        f.addRow(self.wav_same)
        v.addLayout(f)
        b = QPushButton("Preparar")
        b.clicked.connect(self.run_wav)
        v.addWidget(b, 0, Qt.AlignRight)
        return w

    def run_wav(self):
        files, dst = self.wav_files.files(), self.wav_out.path()
        if not self._need(files, dst):
            return
        self.qsettings.setValue("tools/wav_rate", self.wav_rate.value())
        self.qsettings.setValue("tools/wav_comp", "true" if self.wav_comp.isChecked() else "false")
        self.qsettings.setValue("tools/wav_same", "true" if self.wav_same.isChecked() else "false")
        self.main.wav_sfx_job(files, dst, rate=self.wav_rate.value(), compress=self.wav_comp.isChecked(),
                              same_gain=self.wav_same.isChecked())

    # ------------------------------------------------------------- 4. banco .tfi
    def _build_tfi(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        f = QFormLayout()
        self.tfi_src = _DirRow(self, "tools/tfi_src", "Pasta com os arquivos .tfi (subpastas incluídas)")
        lib = self.main.ed_library.text() or str(Path.home())
        self.tfi_dst = _DirRow(self, "tools/tfi_dst", "Pasta onde gravar o banco (de preferência vazia)",
                               str(Path(lib).parent / "banco_tfi"))
        f.addRow("Pasta com os .tfi:", self.tfi_src)
        f.addRow("Pasta do banco:", self.tfi_dst)
        v.addLayout(f)
        tip = QLabel("Junta patches .tfi de jogos e packs (subpastas incluídas) num banco sem repetidos. "
                     "Ao terminar, o campo \"Banco de patches prontos\" da janela principal passa a apontar "
                     "para ele. Milhares de patches levam alguns minutos.")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        b = QPushButton("Montar banco")
        b.clicked.connect(self.run_tfi)
        v.addWidget(b, 0, Qt.AlignRight)
        return w

    def run_tfi(self):
        src, dst = self.tfi_src.path(), self.tfi_dst.path()
        if not src or not dst:
            QMessageBox.information(self, self.windowTitle(), "Escolha a pasta dos .tfi e a pasta do banco.")
            return
        self.main.build_tfi_bank_job(src, dst)
