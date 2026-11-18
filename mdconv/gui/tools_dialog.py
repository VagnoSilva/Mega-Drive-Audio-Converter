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

from mdconv.i18n import tr
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
    QDoubleSpinBox, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from mdconv.ay.convert import AUTO, CH_NAMES, check_routing, dest_label

# destinos oferecidos para cada canal do AY: (texto, valor)
DESTS = ([(tr("Automático"), AUTO)] + [(tr("PSG tom {0}").format(n + 1), ("psg", n)) for n in range(3)]
         + [(tr("FM {0}").format(n + 1), ("fm", n)) for n in range(6)] + [(tr("Omitir (não tocar)"), None)])
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
        for text, slot in ((tr("Adicionar…"), self.add), (tr("Remover"), self.remove), (tr("Limpar"), self.list.clear)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)

    def add(self):
        qs = self.owner.qsettings
        files, _ = QFileDialog.getOpenFileNames(self, self.caption, qs.value(self.key, str(Path.home())),
                                                self.filt + tr(";;Todos (*.*)"))
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
        b = QPushButton(tr("Procurar…"))
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
        self.setWindowTitle(tr("Ferramentas — conversores e efeitos"))
        self.resize(760, 640)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_vgm(), tr("Música VGM (AY / YM2203 / Mega Drive)"))
        self.tabs.addTab(self._build_chip_sfx(), tr("Efeitos de chip (AY)"))
        self.tabs.addTab(self._build_wav(), tr("Efeitos e vozes (WAV)"))
        self.tabs.addTab(self._build_tfi(), tr("Banco de patches .tfi"))
        self.tabs.setCurrentIndex(int(self.qsettings.value("tools/tab", 0) or 0))
        self.tabs.currentChanged.connect(lambda i: self.qsettings.setValue("tools/tab", i))
        v.addWidget(self.tabs, 1)
        note = QLabel(tr("O andamento e os relatórios aparecem no registro da janela principal."))
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
            QMessageBox.information(self, self.windowTitle(), tr("Adicione ao menos um arquivo à lista."))
            return False
        if not dst:
            QMessageBox.information(self, self.windowTitle(), tr("Escolha a pasta de saída."))
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
        g = QGroupBox(tr("Arquivos VGM / VGZ (AY-3-8910, YM2203 ou Mega Drive) ou XGM"))
        gv = QVBoxLayout(g)
        self.vgm_files = _FileList(self, "ay/dir", tr("VGM com AY-3-8910, YM2203 ou Mega Drive, ou XGM do SGDK"), tr("VGM / XGM (*.vgm *.vgz *.xgm)"))
        gv.addWidget(self.vgm_files)
        v.addWidget(g, 1)

        f = QFormLayout()
        self.vgm_out = _DirRow(self, "tools/vgm_out", tr("Pasta onde gravar XGM, VGM e prévias"), self._out_default())
        f.addRow(tr("Pasta de saída:"), self.vgm_out)
        self.vgm_grid = QCheckBox(tr("Travar o andamento na grade de 60 Hz (muda o andamento em até 6%)"))
        self.vgm_grid.setToolTip(tr("Ajusta o andamento para o passo rítmico durar um número inteiro de quadros\n"
                                 "(ritmo sem oscilação no XGM). Desmarcado = andamento original."))
        self.vgm_grid.setChecked(self.qsettings.value("tools/vgm_grid", "false") == "true")
        self.vgm_trim = QCheckBox(tr("Começar na primeira nota (tira o silêncio do início)"))
        self.vgm_trim.setChecked(self.qsettings.value("tools/vgm_trim", "true") != "false")
        f.addRow(self.vgm_grid)
        f.addRow(self.vgm_trim)
        # loop escolhido à mão (vale para todos os arquivos da lista)
        lr = QWidget()
        hl = QHBoxLayout(lr)
        hl.setContentsMargins(0, 0, 0, 0)
        self.vgm_loop = QCheckBox(tr("Escolher: volta para"))
        self.vgm_loop.setToolTip(tr("Põe um loop num VGM que não tem, ou muda o que ele tem. Os tempos contam "
                                    "do começo da música convertida (depois de tirar o silêncio do início, "
                                    "se essa opção estiver marcada)."))
        self.vgm_loop_a = QDoubleSpinBox()
        self.vgm_loop_b = QDoubleSpinBox()
        for sp in (self.vgm_loop_a, self.vgm_loop_b):
            sp.setRange(0.0, 3600.0)
            sp.setDecimals(2)
            sp.setSuffix(" s")
        self.vgm_loop_b.setSpecialValueText(tr("fim da música"))
        self.vgm_loop.setChecked(self.qsettings.value("tools/vgm_loop", "false") == "true")
        self.vgm_loop_a.setValue(float(self.qsettings.value("tools/vgm_loop_a", 0.0) or 0.0))
        self.vgm_loop_b.setValue(float(self.qsettings.value("tools/vgm_loop_b", 0.0) or 0.0))
        hl.addWidget(self.vgm_loop)
        hl.addWidget(self.vgm_loop_a)
        hl.addWidget(QLabel(tr("ao chegar a")))
        hl.addWidget(self.vgm_loop_b)
        hl.addWidget(QLabel(tr("  (desmarcado = o loop do próprio VGM)")))
        hl.addStretch(1)
        f.addRow(tr("Loop:"), lr)
        self.vgm_pcm = QSpinBox()
        self.vgm_pcm.setRange(1, 4)
        self.vgm_pcm.setPrefix("CH")
        self.vgm_pcm.setValue(int(self.qsettings.value("tools/vgm_pcm", 1) or 1))
        self.vgm_pcm.setToolTip(tr("Em qual dos 4 canais PCM do XGM vão as amostras do DAC de um VGM do "
                                   "Mega Drive. Os outros ficam livres para os efeitos do jogo."))
        f.addRow(tr("Canal PCM (VGM do Mega Drive):"), self.vgm_pcm)
        v.addLayout(f)

        g = QGroupBox(tr("Destino de cada canal do AY-3-8910"))
        gv = QVBoxLayout(g)
        tip = QLabel(tr("Vale para todos os arquivos da lista (no YM2203, só o SSG passa por aqui e segue o "
                     "automático). \"Automático\": tom que cabe no PSG fica no PSG; parte grave (abaixo de "
                     "~109 Hz) ou do 2º AY vai para o FM como onda quadrada."))
        tip.setWordWrap(True)
        gv.addWidget(tip)
        self.route_table = QTableWidget(len(AY_KEYS), 3)
        self.route_table.setHorizontalHeaderLabels([tr("Canal do AY"), tr("Destino"), tr("No automático (1º arquivo)")])
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
            self.route_table.setItem(row, 0, QTableWidgetItem(tr("AY{0} — canal {1}").format(c + 1, CH_NAMES[ch])))
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
        b_an = QPushButton(tr("Analisar o 1º arquivo"))
        b_an.setToolTip(tr("Mostra quais canais tocam no 1º arquivo da lista e para onde o automático os mandaria."))
        b_an.clicked.connect(self.analyze_first)
        b_auto = QPushButton(tr("Tudo automático"))
        b_auto.clicked.connect(lambda: [cb.setCurrentIndex(0) for cb in self.route_combos.values()])
        h.addWidget(b_an)
        h.addWidget(b_auto)
        h.addStretch(1)
        gv.addLayout(h)
        v.addWidget(g)

        b = QPushButton(tr("Converter"))
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
            QMessageBox.information(self, self.windowTitle(), tr("Adicione ao menos um arquivo à lista."))
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
                    txt = tr("({0}: não usa esta tabela)").format(chip)
                elif key not in routes:
                    txt = tr("não toca")
                else:
                    txt = dest_label(routes[key])
                self.route_table.item(row, 2).setText(txt)
            for line in report:
                self.main._log("  " + line)

        self.main._run(tr("Analisando {0}").format(Path(path).name), work, done)

    def run_vgm(self):
        files, dst = self.vgm_files.files(), self.vgm_out.path()
        if not self._need(files, dst):
            return
        routing = self.routing()
        errs = check_routing(routing)
        if errs:
            QMessageBox.warning(self, self.windowTitle(), tr("Destinos repetidos:\n\n") + "\n".join(errs))
            return
        self.qsettings.setValue("tools/vgm_grid", "true" if self.vgm_grid.isChecked() else "false")
        self.qsettings.setValue("tools/vgm_trim", "true" if self.vgm_trim.isChecked() else "false")
        if routing:
            self.main._log(tr("Rotas escolhidas: ") + ", ".join(
                tr("AY{0} {1} → {2}").format(c + 1, CH_NAMES[ch], dest_label(d)) for (c, ch), d in sorted(routing.items())))
        loop = self.vgm_loop.isChecked()
        self.qsettings.setValue("tools/vgm_loop", "true" if loop else "false")
        self.qsettings.setValue("tools/vgm_loop_a", self.vgm_loop_a.value())
        self.qsettings.setValue("tools/vgm_loop_b", self.vgm_loop_b.value())
        self.qsettings.setValue("tools/vgm_pcm", self.vgm_pcm.value())
        a, b = self.vgm_loop_a.value(), self.vgm_loop_b.value()
        if loop and b and b <= a:
            QMessageBox.warning(self, self.windowTitle(), tr("O fim do loop tem de vir depois do início."))
            return
        self.main.convert_vgm_job(files, dst, grid=self.vgm_grid.isChecked(), trim=self.vgm_trim.isChecked(),
                                  routing=routing or None, loop_start=a if loop else None,
                                  loop_end=(b or None) if loop else None, pcm_channel=self.vgm_pcm.value())

    # ------------------------------------------------------------- 2. efeitos de chip
    def _build_chip_sfx(self) -> QWidget:
        from mdconv import chipsfx
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox(tr("Efeitos em VGM de AY-3-8910 (um efeito por arquivo)"))
        gv = QVBoxLayout(g)
        self.sfx_files = _FileList(self, "chipsfx/dir", tr("VGM de AY-3-8910 com o efeito"), tr("VGM (*.vgm *.vgz)"))
        gv.addWidget(self.sfx_files)
        v.addWidget(g, 1)
        f = QFormLayout()
        self.sfx_out = _DirRow(self, "tools/chipsfx_out", tr("Pasta onde gravar os efeitos"), self._out_default())
        f.addRow(tr("Pasta de saída:"), self.sfx_out)
        row, self.sfx_psg = self._checks(tr("Tom {}"), 3, chipsfx.DEFAULT_PSG)
        self._load_checks("tools/chipsfx_psg", self.sfx_psg, chipsfx.DEFAULT_PSG)
        f.addRow(tr("Canais PSG livres:"), row)
        row, self.sfx_fm = self._checks(tr("FM {}"), 6, chipsfx.DEFAULT_FM)
        self._load_checks("tools/chipsfx_fm", self.sfx_fm, chipsfx.DEFAULT_FM)
        f.addRow(tr("Canais FM livres:"), row)
        v.addLayout(f)
        tip = QLabel(tr("Marque os canais que a música do jogo deixa livres para efeitos. Os tons do efeito "
                     "ocupam os canais PSG marcados, em ordem; tons graves demais para o PSG vão para os FM "
                     "marcados, como onda quadrada. O FM 6 costuma estar com o PCM do XGM. "
                     "Sai .vgm, prévia .wav e um .c para o SGDK com nome_play() e nome_update()."))
        tip.setWordWrap(True)
        v.addWidget(tip)
        b = QPushButton(tr("Converter"))
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
            QMessageBox.information(self, self.windowTitle(), tr("Marque ao menos um canal livre (PSG ou FM)."))
            return
        self.qsettings.setValue("tools/chipsfx_psg", ",".join(map(str, psg)))
        self.qsettings.setValue("tools/chipsfx_fm", ",".join(map(str, fm)))
        self.main.chip_sfx_files_job(files, dst, psg, fm)

    # ------------------------------------------------------------- 3. WAV
    def _build_wav(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox(tr("WAVs de efeitos sonoros e vozes"))
        gv = QVBoxLayout(g)
        self.wav_files = _FileList(self, "sfx/dir", tr("WAVs de efeitos e vozes"), tr("WAV (*.wav)"))
        gv.addWidget(self.wav_files)
        v.addWidget(g, 1)
        f = QFormLayout()
        self.wav_out = _DirRow(self, "tools/wav_out", tr("Pasta onde gravar os WAVs prontos para o SGDK"),
                               self._out_default())
        f.addRow(tr("Pasta de saída:"), self.wav_out)
        self.wav_rate = QSpinBox()
        self.wav_rate.setRange(4000, 32000)
        self.wav_rate.setSingleStep(50)
        self.wav_rate.setSuffix(tr(" Hz"))
        self.wav_rate.setValue(int(self.qsettings.value("tools/wav_rate", 14000) or 14000))
        self.wav_rate.setToolTip(tr("14000 Hz = XGM v1."))
        f.addRow(tr("Taxa do driver:"), self.wav_rate)
        self.wav_comp = QCheckBox(tr("Comprimir levemente a dinâmica (menos chiado nos finais; recomendado)"))
        self.wav_comp.setChecked(self.qsettings.value("tools/wav_comp", "true") != "false")
        self.wav_same = QCheckBox(tr("Manter o volume relativo entre os arquivos (mesmo ganho para todos)"))
        self.wav_same.setToolTip(tr("Marcado: um passo continua mais baixo que uma explosão (os sons fracos ficam "
                                 "com um pouco mais de chiado).\nDesmarcado: cada som no volume máximo."))
        self.wav_same.setChecked(self.qsettings.value("tools/wav_same", "false") == "true")
        f.addRow(self.wav_comp)
        f.addRow(self.wav_same)
        v.addLayout(f)
        b = QPushButton(tr("Preparar"))
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
        self.tfi_src = _DirRow(self, "tools/tfi_src", tr("Pasta com os arquivos .tfi (subpastas incluídas)"))
        lib = self.main.ed_library.text() or str(Path.home())
        self.tfi_dst = _DirRow(self, "tools/tfi_dst", tr("Pasta onde gravar o banco (de preferência vazia)"),
                               str(Path(lib).parent / "banco_tfi"))
        f.addRow(tr("Pasta com os .tfi:"), self.tfi_src)
        f.addRow(tr("Pasta do banco:"), self.tfi_dst)
        v.addLayout(f)
        tip = QLabel(tr("Junta patches .tfi de jogos e packs (subpastas incluídas) num banco sem repetidos. "
                     "Ao terminar, o campo \"Banco de patches prontos\" da janela principal passa a apontar "
                     "para ele. Milhares de patches levam alguns minutos."))
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        b = QPushButton(tr("Montar banco"))
        b.clicked.connect(self.run_tfi)
        v.addWidget(b, 0, Qt.AlignRight)
        return w

    def run_tfi(self):
        src, dst = self.tfi_src.path(), self.tfi_dst.path()
        if not src or not dst:
            QMessageBox.information(self, self.windowTitle(), tr("Escolha a pasta dos .tfi e a pasta do banco."))
            return
        self.main.build_tfi_bank_job(src, dst)
