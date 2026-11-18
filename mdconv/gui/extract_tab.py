"""Aba "Extração": tira MIDI + SoundFont de ROMs (Capcom CPS1 e Game Boy Advance).

Funciona sozinha, como o extrator independente. O botão "Abrir na conversão" leva a música
escolhida (MIDI + SF2 + biblioteca, quando houver) para a aba "Conversão"."""
from __future__ import annotations

from mdconv.i18n import tr
import glob
import os
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QRadioButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from mdconv import extract


class _Signals(QObject):
    log = Signal(str)
    prog = Signal(int, int)
    done = Signal(object)


class _Job(QRunnable):
    def __init__(self, game, out_dir, opts):
        super().__init__()
        self.game, self.out_dir, self.opts = game, out_dir, opts
        self.signals = _Signals()

    def run(self):
        try:
            base = extract.run_export(
                self.game, self.out_dir,
                log=lambda m: self.signals.log.emit(str(m)),
                progress=lambda stage, a, b: self.signals.prog.emit(int(a), int(max(1, b))),
                **self.opts)
            self.signals.done.emit(base)
        except Exception:  # noqa: BLE001 — o erro aparece no registro
            self.signals.log.emit(traceback.format_exc())
            self.signals.done.emit(None)


class ExtractTab(QWidget):
    """``open_in_converter(midi, sf2, library_or_None)`` é chamado pelo botão de levar à conversão."""

    def __init__(self, open_in_converter=None, parent=None):
        super().__init__(parent)
        self.open_in_converter = open_in_converter
        self.qs = QSettings("mdconv", "Mega Drive Audio Converter")
        self.game = None
        self.song_ids: list[int] = []
        self.job: _Job | None = None
        self.last_base: str | None = None
        self.pool = QThreadPool.globalInstance()
        self._build()
        self.ed_rom.setText(self.qs.value("extract/rom", ""))
        self.ed_out.setText(self.qs.value("extract/out", str(Path.home() / "Desktop" / "Audio_extraido")))
        last = self.qs.value("extract/last_base", "")
        if last and os.path.isdir(last):
            self.last_base = last
            self.bt_open.setEnabled(True)
            self._refresh_outputs(last)

    # ------------------------------------------------------------- layout
    def _build(self):
        v = QVBoxLayout(self)

        g1 = QGroupBox(tr("1. ROM do jogo — .zip do MAME ou pasta (CPS1), .gba (Game Boy Advance)"))
        l1 = QVBoxLayout(g1)
        r1 = QHBoxLayout()
        self.ed_rom = QLineEdit()
        self.ed_rom.setPlaceholderText(tr("Escolha o arquivo da ROM…"))
        b_file, b_dir = QPushButton(tr("Arquivo…")), QPushButton(tr("Pasta…"))
        b_file.clicked.connect(self.pick_file)
        b_dir.clicked.connect(self.pick_dir)
        r1.addWidget(self.ed_rom, 1)
        r1.addWidget(b_file)
        r1.addWidget(b_dir)
        l1.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel(tr("Jogo CPS1:")))
        self.cb_game = QComboBox()
        self.cb_game.setMinimumWidth(180)
        self.cb_game.addItem("")
        try:
            self.cb_game.addItems(extract.cps1_games())
        except Exception:  # noqa: BLE001
            pass
        self.cb_game.setToolTip(tr("Só para CPS1: o jogo é reconhecido pelo nome do zip; escolha aqui se não for."))
        r2.addWidget(self.cb_game)
        self.bt_load = QPushButton(tr("Carregar"))
        self.bt_load.clicked.connect(self.load)
        r2.addWidget(self.bt_load)
        self.lb_info = QLabel(tr("{0} jogos CPS1 + jogos de GBA com o motor de som da Nintendo + Irem (Moon Patrol, Kung-Fu "
                                 "Master, Lode Runner), Ghosts 'n Goblins, Nemesis e Neo Geo (KOF 94 a 98), direto para o "
                                 "Mega Drive").format(self.cb_game.count() - 1))
        self.lb_info.setStyleSheet("color: #888; margin-left: 12px")
        r2.addWidget(self.lb_info, 1)
        l1.addLayout(r2)
        v.addWidget(g1)

        # ---- meio: músicas | opções + saída
        mid = QHBoxLayout()
        g2 = QGroupBox(tr("2. Músicas"))
        l2 = QHBoxLayout(g2)
        self.ls_songs = QListWidget()
        self.ls_songs.setSelectionMode(QAbstractItemView.ExtendedSelection)
        l2.addWidget(self.ls_songs, 1)
        bb = QVBoxLayout()
        b_all, b_none = QPushButton(tr("Todas")), QPushButton(tr("Nenhuma"))
        b_all.clicked.connect(self.ls_songs.selectAll)
        b_none.clicked.connect(self.ls_songs.clearSelection)
        bb.addWidget(b_all)
        bb.addWidget(b_none)
        bb.addStretch(1)
        l2.addLayout(bb)
        mid.addWidget(g2, 1)

        right = QVBoxLayout()
        g3 = QGroupBox(tr("3. O que gerar"))
        l3 = QVBoxLayout(g3)
        self.ck_midi = QCheckBox(tr("MIDI das músicas"))
        self.ck_sf2 = QCheckBox(tr("SoundFont (.sf2)"))
        self.ck_wav = QCheckBox(tr("Samples do Oki (.wav) — CPS1"))
        self.ck_lib = QCheckBox(tr("Biblioteca de patches FM — CPS1"))
        for c in (self.ck_midi, self.ck_sf2, self.ck_wav, self.ck_lib):
            c.setChecked(True)
            l3.addWidget(c)
        l3.addWidget(QLabel(tr("Qualidade do SF2 (CPS1):")))
        self.rb_full = QRadioButton(tr("Completa (3 amostras/instr.)"))
        self.rb_quick = QRadioButton(tr("Rápida (1 amostra/instr.)"))
        self.rb_full.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_full)
        grp.addButton(self.rb_quick)
        l3.addWidget(self.rb_full)
        l3.addWidget(self.rb_quick)
        hp = QHBoxLayout()
        hp.addWidget(QLabel(tr("Repetições do loop (CPS1):")))
        self.sp_plays = QSpinBox()
        self.sp_plays.setRange(1, 8)
        self.sp_plays.setValue(2)
        hp.addWidget(self.sp_plays)
        hp.addStretch(1)
        l3.addLayout(hp)
        self._cps1_only = [self.ck_wav, self.ck_lib, self.rb_full, self.rb_quick, self.sp_plays]
        self.bt_sfx_ch = QPushButton(tr("Canais dos efeitos…"))
        self.bt_sfx_ch.setToolTip(tr("Escolhe em quais canais do PSG e do FM cada efeito de chip toca, para "
                                  "efeitos que soam juntos no jogo não brigarem pelo mesmo canal."))
        self.bt_sfx_ch.clicked.connect(self.edit_sfx_channels)
        self.bt_sfx_ch.setEnabled(False)
        l3.addWidget(self.bt_sfx_ch)
        self.bt_neo = QPushButton(tr("Opções do Neo Geo…"))
        self.bt_neo.setToolTip(tr("Canais PCM da música, peças metálicas no ruído do PSG e tamanho das amostras "
                               "(o que mais pesa na ROM)."))
        self.bt_neo.clicked.connect(self.edit_neo_options)
        self.bt_neo.setEnabled(False)
        l3.addWidget(self.bt_neo)
        right.addWidget(g3)

        g4 = QGroupBox(tr("4. Pasta de saída"))
        l4 = QVBoxLayout(g4)
        r4 = QHBoxLayout()
        self.ed_out = QLineEdit()
        b_out = QPushButton(tr("Procurar…"))
        b_out.clicked.connect(self.pick_out)
        r4.addWidget(self.ed_out, 1)
        r4.addWidget(b_out)
        l4.addLayout(r4)
        r5 = QHBoxLayout()
        self.bt_go = QPushButton(tr("Extrair"))
        self.bt_go.setMinimumHeight(36)
        self.bt_go.setStyleSheet("font-weight: bold")
        self.bt_go.clicked.connect(self.run)
        self.bt_open = QPushButton(tr("Abrir pasta"))
        self.bt_open.setMinimumHeight(36)
        self.bt_open.clicked.connect(self.open_folder)
        self.bt_open.setEnabled(False)
        r5.addWidget(self.bt_go, 2)
        r5.addWidget(self.bt_open, 1)
        l4.addLayout(r5)
        right.addWidget(g4)
        right.addStretch(1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(380)
        mid.addWidget(rw)
        top = QWidget()
        top.setLayout(mid)

        # ---- baixo: registro | levar para a conversão
        self.prog = QProgressBar()
        self.prog.setTextVisible(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText(tr("O registro da extração aparece aqui."))

        g5 = QGroupBox(tr("5. Levar para a conversão"))
        l5 = QVBoxLayout(g5)
        self.ls_out = QListWidget()
        self.ls_out.itemDoubleClicked.connect(lambda _it: self.send_to_converter())
        l5.addWidget(self.ls_out, 1)
        self.lb_out = QLabel(tr("Extraia um jogo para ver os MIDIs aqui."))
        self.lb_out.setWordWrap(True)
        self.lb_out.setStyleSheet("color: #888")
        l5.addWidget(self.lb_out)
        self.bt_send = QPushButton(tr("Abrir na conversão →"))
        self.bt_send.setMinimumHeight(32)
        self.bt_send.clicked.connect(self.send_to_converter)
        self.bt_send.setEnabled(False)
        l5.addWidget(self.bt_send)

        bottom = QSplitter(Qt.Horizontal)
        logw = QGroupBox(tr("Registro"))
        lv = QVBoxLayout(logw)
        lv.addWidget(self.prog)
        lv.addWidget(self.log, 1)
        bottom.addWidget(logw)
        bottom.addWidget(g5)
        bottom.setStretchFactor(0, 3)
        bottom.setStretchFactor(1, 2)

        split = QSplitter(Qt.Vertical)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        v.addWidget(split, 1)

    # ------------------------------------------------------------- ações
    def pick_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, tr("Escolha a ROM"), self.ed_rom.text() or str(Path.home()),
            tr("ROMs (*.zip *.gba *.agb);;Romset do MAME (CPS1) (*.zip);;Game Boy Advance (*.gba *.agb);;Todos (*.*)"))
        if p:
            self._set_rom(p)

    def pick_dir(self):
        p = QFileDialog.getExistingDirectory(self, tr("Pasta com as ROMs (CPS1)"), self.ed_rom.text() or str(Path.home()))
        if p:
            self._set_rom(p)

    def pick_out(self):
        p = QFileDialog.getExistingDirectory(self, tr("Pasta de saída"), self.ed_out.text() or str(Path.home()))
        if p:
            self.ed_out.setText(p)

    def _set_rom(self, p):
        self.ed_rom.setText(p)
        if extract.is_gba(p) or extract.is_direct(p):
            self.cb_game.setCurrentIndex(0)
            self.load()
            return
        g = extract.guess_cps1_game(p)
        if g:
            self.cb_game.setCurrentText(g)
            self.load()
        else:
            self.lb_info.setText(tr("Escolha o jogo CPS1 na lista e clique em Carregar."))

    def load(self):
        rom = self.ed_rom.text().strip()
        if not rom:
            QMessageBox.information(self, tr("Extração"), tr("Escolha primeiro a ROM (.zip, .gba ou pasta)."))
            return
        try:
            self.game = extract.open_rom(rom, self.cb_game.currentText().strip() or None)
        except Exception as e:  # noqa: BLE001
            self.game = None
            QMessageBox.critical(self, tr("Erro ao carregar"), str(e))
            return
        self.qs.setValue("extract/rom", rom)
        plat = extract.platform(self.game)
        gba = plat != "CPS1"
        mp = plat == "Direto"
        if plat == "CPS1":
            self.cb_game.setCurrentText(self.game.name)
        self.ls_songs.clear()
        self.song_ids = []
        for sid, _ptr, n in self.game.songs():
            kind = tr("{0} faixas").format(n) if n > 1 else tr("1 faixa")
            self.ls_songs.addItem(extract.song_label(self.game, sid) if mp
                                  else f"{extract.song_label(self.game, sid)}  ({kind})")
            self.song_ids.append(sid)
        self.ls_songs.selectAll()
        for w in self._cps1_only:
            w.setEnabled(not gba)
        for w in (self.ck_midi, self.ck_sf2, self.ck_wav):
            w.setEnabled(not mp)
        self.bt_sfx_ch.setEnabled(hasattr(self.game, "effects"))
        self.bt_neo.setEnabled(hasattr(self.game, "neo_options"))
        self.lb_info.setText(extract.summary(self.game))
        self.log.clear()
        for w in self.game.warnings:
            self.log.appendPlainText(tr("Aviso: ") + w)

    def run(self):
        if self.job is not None:
            return
        if not self.game:
            self.load()
            if not self.game:
                return
        sel = sorted(self.song_ids[i.row()] for i in self.ls_songs.selectedIndexes())
        mp = extract.platform(self.game) == "Direto"
        if (self.ck_midi.isChecked() or mp) and not sel:
            QMessageBox.information(self, tr("Extração"), tr("Selecione ao menos uma música."))
            return
        out = self.ed_out.text().strip() or str(Path.home() / "Desktop" / "Audio_extraido")
        self.qs.setValue("extract/out", out)
        opts = dict(plays=self.sp_plays.value(), do_midi=self.ck_midi.isChecked(),
                    do_sf2=self.ck_sf2.isChecked(), do_wav=self.ck_wav.isChecked(),
                    do_mdlib=self.ck_lib.isChecked(),
                    quality="full" if self.rb_full.isChecked() else "quick",
                    song_ids=set(sel) if len(sel) < len(self.song_ids) else None)
        if hasattr(self.game, "effects"):
            opts["sfx_channels"] = self._sfx_channels()
        if hasattr(self.game, "neo_options"):
            opts["neo_options"] = self._neo_options()
        self.log.clear()
        self.prog.setValue(0)
        self.bt_go.setEnabled(False)
        self.job = _Job(self.game, out, opts)
        self.job.signals.log.connect(self.log.appendPlainText)
        self.job.signals.prog.connect(lambda a, b: (self.prog.setMaximum(b), self.prog.setValue(a)))
        self.job.signals.done.connect(self._done)
        self.pool.start(self.job)

    # ------------------------------------------------------------- canais dos efeitos
    def _sfx_key(self) -> str:
        return f"extract/sfx_channels/{getattr(getattr(self.game, 'board', None), 'key', self.game.name)}"

    def _sfx_channels(self) -> dict:
        """{comando: (tons do PSG, canais FM)}: o escolhido (salvo por jogo) ou o padrão do jogo."""
        import json
        chosen = {}
        try:
            chosen = {int(k): (tuple(v[0]), tuple(v[1]))
                      for k, v in json.loads(self.qs.value(self._sfx_key(), "{}") or "{}").items()}
        except (ValueError, TypeError, IndexError):
            pass
        return {cmd: chosen.get(cmd, dflt) for cmd, _name, dflt in self.game.effects()}

    def edit_sfx_channels(self):
        import json
        if not hasattr(self.game, "effects"):
            return
        dlg = SfxChannelsDialog(self.game, self._sfx_channels(), self)
        if dlg.exec():
            self.qs.setValue(self._sfx_key(), json.dumps(
                {str(c): [list(p), list(f)] for c, (p, f) in dlg.result_channels().items()}))
            self.log.appendPlainText(tr("Canais dos efeitos salvos para ") + self.game.name + ".")

    # ------------------------------------------------------------- opções do Neo Geo
    def _neo_options(self) -> dict:
        import json
        opt = self.game.neo_options()
        try:
            opt.update(json.loads(self.qs.value("extract/neo_options", "{}") or "{}"))
        except (ValueError, TypeError):
            pass
        return opt

    def edit_neo_options(self):
        import json
        if not hasattr(self.game, "neo_options"):
            return
        dlg = NeoOptionsDialog(self._neo_options(), self.game.neo_options(), self)
        if dlg.exec():
            self.qs.setValue("extract/neo_options", json.dumps(dlg.result_options()))
            self.log.appendPlainText(tr("Opções do Neo Geo salvas."))

    def _done(self, base):
        self.job = None
        self.bt_go.setEnabled(True)
        if base:
            self.prog.setValue(self.prog.maximum())
            self.last_base = base
            self.qs.setValue("extract/last_base", base)
            self.bt_open.setEnabled(True)
            self._refresh_outputs(base)

    def _refresh_outputs(self, base):
        self.ls_out.clear()
        self.bt_send.setEnabled(False)
        if not base or not os.path.isdir(base):
            return
        o = extract.outputs(base)
        self._outs = o
        if not o.midis and (os.path.isdir(os.path.join(base, "musicas"))
                            or os.path.isdir(os.path.join(base, "efeitos"))):
            n_m = len(glob.glob(os.path.join(base, "musicas", "*.xgm")))
            n_e = len(glob.glob(os.path.join(base, "efeitos", "*.c")))
            n_v = len(glob.glob(os.path.join(base, "vozes", "*.wav")))
            for f in sorted(glob.glob(os.path.join(base, "musicas", "*.xgm")) + glob.glob(os.path.join(base, "efeitos", "*.c"))
                            + glob.glob(os.path.join(base, "vozes", "*.wav"))):
                self.ls_out.addItem(os.path.relpath(f, base))
            self.lb_out.setText(tr("{0} música(s) em XGM/VGM, {1} efeito(s) com código C e {2} voz(es) em WAV em {3}\nJá estão "
                                   "prontos para o Mega Drive: não precisam passar pela aba Conversão.").format(n_m, n_e, n_v, base))
            return
        for m in o.midis:
            self.ls_out.addItem(os.path.basename(m))
        if o.midis:
            self.ls_out.setCurrentRow(0)
            self.bt_send.setEnabled(bool(self.open_in_converter))
        info = tr("{0} MIDI(s) em {1}").format(len(o.midis), base)
        info += tr("\nSoundFont: {0}").format(os.path.basename(o.sf2)) if o.sf2 else tr("\nSem SoundFont: marque “SoundFont (.sf2)”.")
        if o.library:
            info += tr("\nBiblioteca de patches FM do jogo: vai junto para a conversão.")
        self.lb_out.setText(info)

    def send_to_converter(self):
        o = getattr(self, "_outs", None)
        row = self.ls_out.currentRow()
        if not o or row < 0 or row >= len(o.midis) or not self.open_in_converter:
            return
        if not o.sf2:
            QMessageBox.information(self, tr("Extração"), tr("Gere também o SoundFont (.sf2) para converter."))
            return
        self.open_in_converter(o.midis[row], o.sf2, o.library)

    def open_folder(self):
        if self.last_base and os.path.isdir(self.last_base):
            from mdconv.gui.main_window import open_with_system
            open_with_system(Path(self.last_base))

    def busy(self) -> bool:
        return self.job is not None


class SfxChannelsDialog(QDialog):
    """Tabela: um efeito por linha, com os canais de tom do PSG e os canais FM que ele pode usar."""
    N_PSG, N_FM = 3, 6

    def __init__(self, game, channels: dict, parent=None):
        super().__init__(parent)
        self.game = game
        self.setWindowTitle(tr("Canais dos efeitos — {0}").format(game.name))
        self.resize(900, 420)
        v = QVBoxLayout(self)
        tip = QLabel(tr("Cada efeito toca só nos canais marcados. Tons que cabem no PSG usam os tons do PSG "
                     "marcados; o que não couber (ou se nenhum tom do PSG estiver marcado) vai para os FM "
                     "marcados, como onda quadrada. Efeitos que soam ao mesmo tempo no jogo (ex.: pulo e "
                     "tiro) devem ficar em canais diferentes. O ruído sempre usa o 4º canal do PSG. "
                     "Deixe livres os canais que a música usa (o FM 6 costuma ser o PCM do XGM)."))
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.effects = game.effects()
        heads = ([tr("Efeito")] + [tr("PSG {0}").format(i + 1) for i in range(self.N_PSG)]
                 + [tr("FM {0}").format(i + 1) for i in range(self.N_FM)] + [tr("Divide canal com")])
        self.table = QTableWidget(len(self.effects), len(heads))
        self.conflict_col = len(heads) - 1
        self.table.setHorizontalHeaderLabels(heads)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(heads) - 1, QHeaderView.Stretch)
        self.checks = {}
        for row, (cmd, name, _dflt) in enumerate(self.effects):
            self.table.setItem(row, 0, QTableWidgetItem(f"{cmd:02X} {name}"))
            psg, fm = channels.get(cmd, _dflt)
            boxes = []
            for col in range(self.N_PSG + self.N_FM):
                ck = QCheckBox()
                on = (col in psg) if col < self.N_PSG else (col - self.N_PSG in fm)
                ck.setChecked(on)
                ck.toggled.connect(self._update_conflicts)
                cell = QWidget()
                h = QHBoxLayout(cell)
                h.setContentsMargins(0, 0, 0, 0)
                h.setAlignment(Qt.AlignCenter)
                h.addWidget(ck)
                self.table.setCellWidget(row, 1 + col, cell)
                boxes.append(ck)
            self.checks[cmd] = boxes
            self.table.setItem(row, len(heads) - 1, QTableWidgetItem(""))
        v.addWidget(self.table, 1)
        h = QHBoxLayout()
        b = QPushButton(tr("Padrão do jogo"))
        b.clicked.connect(self.reset)
        h.addWidget(b)
        h.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        h.addWidget(bb)
        v.addLayout(h)
        self._update_conflicts()

    def result_channels(self) -> dict:
        out = {}
        for cmd, boxes in self.checks.items():
            psg = tuple(i for i in range(self.N_PSG) if boxes[i].isChecked())
            fm = tuple(i for i in range(self.N_FM) if boxes[self.N_PSG + i].isChecked())
            out[cmd] = (psg, fm)
        return out

    def reset(self):
        for cmd, _name, (psg, fm) in self.effects:
            for i, ck in enumerate(self.checks[cmd]):
                ck.setChecked((i in psg) if i < self.N_PSG else (i - self.N_PSG in fm))

    def _update_conflicts(self, *_):
        res = self.result_channels()
        names = {cmd: f"{cmd:02X}" for cmd, _n, _d in self.effects}
        col = self.conflict_col
        for row, (cmd, _name, _d) in enumerate(self.effects):
            psg, fm = res[cmd]
            mine = {("p", c) for c in psg} | {("f", c) for c in fm}
            others = [names[o] for o, (p2, f2) in res.items() if o != cmd
                      and mine & ({("p", c) for c in p2} | {("f", c) for c in f2})]
            self.table.item(row, col).setText(", ".join(others))

    def _accept(self):
        empty = [f"{cmd:02X} {name}" for cmd, name, _d in self.effects if self.result_channels()[cmd] == ((), ())]
        if empty:
            QMessageBox.warning(self, self.windowTitle(), tr("Marque ao menos um canal para:\n") + "\n".join(empty))
            return
        self.accept()


class NeoOptionsDialog(QDialog):
    """Opções da conversão do Neo Geo: onde vão as amostras e quanto elas pesam na ROM."""

    def __init__(self, opt: dict, default: dict, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout
        self.default = default
        self.setWindowTitle(tr("Opções do Neo Geo"))
        v = QVBoxLayout(self)
        tip = QLabel(tr("As amostras ADPCM do Neo Geo viram PCM do XGM (8 bits, 14 kHz), gravadas dentro de cada "
                     "música — é o que mais pesa na ROM. O relatório de cada música mostra quantos KB ela usa."))
        tip.setWordWrap(True)
        v.addWidget(tip)
        f = QFormLayout()
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.ck_ch = []
        for c in (1, 2, 3, 4):
            ck = QCheckBox(f"CH{c}")
            self.ck_ch.append(ck)
            h.addWidget(ck)
        h.addWidget(QLabel(tr("  (deixe livres os canais das vozes e golpes do jogo)")))
        h.addStretch(1)
        f.addRow(tr("Canais PCM da música:"), row)
        self.ck_metal = QCheckBox(tr("Chimbais e pratos no ruído do PSG (economiza PCM e ROM)"))
        f.addRow(self.ck_metal)
        self.ck_b = QCheckBox(tr("ADPCM-B (instrumento com altura) no FM 5, com o timbre tirado da amostra"))
        f.addRow(self.ck_b)
        self.sp_len = QDoubleSpinBox()
        self.sp_len.setRange(0.1, 3.0)
        self.sp_len.setSingleStep(0.1)
        self.sp_len.setSuffix(" s")
        f.addRow(tr("Duração máxima de cada amostra:"), self.sp_len)
        self.cb_step = QComboBox()
        for txt, val in ((tr("3 dB (mais variações, mais ROM)"), 3.0), (tr("6 dB"), 6.0), (tr("12 dB (menos ROM)"), 12.0)):
            self.cb_step.addItem(txt, val)
        f.addRow(tr("Volume gravado na amostra, em passos de:"), self.cb_step)
        self.sp_min = QDoubleSpinBox()
        self.sp_min.setRange(-40, 0)
        self.sp_min.setSuffix(tr(" dB"))
        f.addRow(tr("Notas mais baixas que (ecos) ficam de fora:"), self.sp_min)
        v.addLayout(f)
        hb = QHBoxLayout()
        b = QPushButton(tr("Padrão"))
        b.clicked.connect(lambda: self._set(self.default))
        hb.addWidget(b)
        hb.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        hb.addWidget(bb)
        v.addLayout(hb)
        self._set(opt)

    def _set(self, o: dict):
        for c, ck in enumerate(self.ck_ch, 1):
            ck.setChecked(c in o.get("pcm_channels", (1, 2)))
        self.ck_metal.setChecked(bool(o.get("metals_to_noise", True)))
        self.ck_b.setChecked(bool(o.get("adpcm_b_to_fm", True)))
        self.sp_len.setValue(float(o.get("max_sample_s", 0.8)))
        self.cb_step.setCurrentIndex(max(0, self.cb_step.findData(float(o.get("volume_step_db", 6.0)))))
        self.sp_min.setValue(float(o.get("min_level_db", -15.0)))

    def result_options(self) -> dict:
        return dict(pcm_channels=[c for c, ck in enumerate(self.ck_ch, 1) if ck.isChecked()],
                    metals_to_noise=self.ck_metal.isChecked(), adpcm_b_to_fm=self.ck_b.isChecked(),
                    max_sample_s=round(self.sp_len.value(), 2), volume_step_db=float(self.cb_step.currentData()),
                    min_level_db=float(self.sp_min.value()))

    def _accept(self):
        if not any(ck.isChecked() for ck in self.ck_ch):
            QMessageBox.warning(self, self.windowTitle(), tr("Marque ao menos um canal PCM para a música."))
            return
        self.accept()
