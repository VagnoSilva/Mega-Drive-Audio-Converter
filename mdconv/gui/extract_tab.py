"""Aba "Extração": tira MIDI + SoundFont de ROMs (Capcom CPS1 e Game Boy Advance).

Funciona sozinha, como o extrator independente. O botão "Abrir na conversão" leva a música
escolhida (MIDI + SF2 + biblioteca, quando houver) para a aba "Conversão"."""
from __future__ import annotations

import os
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
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

        g1 = QGroupBox("1. ROM do jogo — .zip do MAME ou pasta (CPS1), .gba (Game Boy Advance)")
        l1 = QVBoxLayout(g1)
        r1 = QHBoxLayout()
        self.ed_rom = QLineEdit()
        self.ed_rom.setPlaceholderText("Escolha o arquivo da ROM…")
        b_file, b_dir = QPushButton("Arquivo…"), QPushButton("Pasta…")
        b_file.clicked.connect(self.pick_file)
        b_dir.clicked.connect(self.pick_dir)
        r1.addWidget(self.ed_rom, 1)
        r1.addWidget(b_file)
        r1.addWidget(b_dir)
        l1.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Jogo CPS1:"))
        self.cb_game = QComboBox()
        self.cb_game.setMinimumWidth(180)
        self.cb_game.addItem("")
        try:
            self.cb_game.addItems(extract.cps1_games())
        except Exception:  # noqa: BLE001
            pass
        self.cb_game.setToolTip("Só para CPS1: o jogo é reconhecido pelo nome do zip; escolha aqui se não for.")
        r2.addWidget(self.cb_game)
        self.bt_load = QPushButton("Carregar")
        self.bt_load.clicked.connect(self.load)
        r2.addWidget(self.bt_load)
        self.lb_info = QLabel(f"{self.cb_game.count() - 1} jogos CPS1 + jogos de GBA com o motor de som da Nintendo")
        self.lb_info.setStyleSheet("color: #888; margin-left: 12px")
        r2.addWidget(self.lb_info, 1)
        l1.addLayout(r2)
        v.addWidget(g1)

        # ---- meio: músicas | opções + saída
        mid = QHBoxLayout()
        g2 = QGroupBox("2. Músicas")
        l2 = QHBoxLayout(g2)
        self.ls_songs = QListWidget()
        self.ls_songs.setSelectionMode(QAbstractItemView.ExtendedSelection)
        l2.addWidget(self.ls_songs, 1)
        bb = QVBoxLayout()
        b_all, b_none = QPushButton("Todas"), QPushButton("Nenhuma")
        b_all.clicked.connect(self.ls_songs.selectAll)
        b_none.clicked.connect(self.ls_songs.clearSelection)
        bb.addWidget(b_all)
        bb.addWidget(b_none)
        bb.addStretch(1)
        l2.addLayout(bb)
        mid.addWidget(g2, 1)

        right = QVBoxLayout()
        g3 = QGroupBox("3. O que gerar")
        l3 = QVBoxLayout(g3)
        self.ck_midi = QCheckBox("MIDI das músicas")
        self.ck_sf2 = QCheckBox("SoundFont (.sf2)")
        self.ck_wav = QCheckBox("Samples do Oki (.wav) — CPS1")
        self.ck_opm = QCheckBox("Timbres FM (.opm / VOPM) — CPS1")
        self.ck_lib = QCheckBox("Biblioteca de patches FM — CPS1")
        for c in (self.ck_midi, self.ck_sf2, self.ck_wav, self.ck_opm, self.ck_lib):
            c.setChecked(True)
            l3.addWidget(c)
        l3.addWidget(QLabel("Qualidade do SF2 (CPS1):"))
        self.rb_full = QRadioButton("Completa (3 amostras/instr.)")
        self.rb_quick = QRadioButton("Rápida (1 amostra/instr.)")
        self.rb_full.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_full)
        grp.addButton(self.rb_quick)
        l3.addWidget(self.rb_full)
        l3.addWidget(self.rb_quick)
        hp = QHBoxLayout()
        hp.addWidget(QLabel("Repetições do loop (CPS1):"))
        self.sp_plays = QSpinBox()
        self.sp_plays.setRange(1, 8)
        self.sp_plays.setValue(2)
        hp.addWidget(self.sp_plays)
        hp.addStretch(1)
        l3.addLayout(hp)
        self._cps1_only = [self.ck_wav, self.ck_opm, self.ck_lib, self.rb_full, self.rb_quick, self.sp_plays]
        right.addWidget(g3)

        g4 = QGroupBox("4. Pasta de saída")
        l4 = QVBoxLayout(g4)
        r4 = QHBoxLayout()
        self.ed_out = QLineEdit()
        b_out = QPushButton("Procurar…")
        b_out.clicked.connect(self.pick_out)
        r4.addWidget(self.ed_out, 1)
        r4.addWidget(b_out)
        l4.addLayout(r4)
        r5 = QHBoxLayout()
        self.bt_go = QPushButton("Extrair")
        self.bt_go.setMinimumHeight(36)
        self.bt_go.setStyleSheet("font-weight: bold")
        self.bt_go.clicked.connect(self.run)
        self.bt_open = QPushButton("Abrir pasta")
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
        self.log.setPlaceholderText("O registro da extração aparece aqui.")

        g5 = QGroupBox("5. Levar para a conversão")
        l5 = QVBoxLayout(g5)
        self.ls_out = QListWidget()
        self.ls_out.itemDoubleClicked.connect(lambda _it: self.send_to_converter())
        l5.addWidget(self.ls_out, 1)
        self.lb_out = QLabel("Extraia um jogo para ver os MIDIs aqui.")
        self.lb_out.setWordWrap(True)
        self.lb_out.setStyleSheet("color: #888")
        l5.addWidget(self.lb_out)
        self.bt_send = QPushButton("Abrir na conversão →")
        self.bt_send.setMinimumHeight(32)
        self.bt_send.clicked.connect(self.send_to_converter)
        self.bt_send.setEnabled(False)
        l5.addWidget(self.bt_send)

        bottom = QSplitter(Qt.Horizontal)
        logw = QGroupBox("Registro")
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
            self, "Escolha a ROM", self.ed_rom.text() or str(Path.home()),
            "ROMs (*.zip *.gba *.agb);;Romset do MAME (CPS1) (*.zip);;Game Boy Advance (*.gba *.agb);;Todos (*.*)")
        if p:
            self._set_rom(p)

    def pick_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Pasta com as ROMs (CPS1)", self.ed_rom.text() or str(Path.home()))
        if p:
            self._set_rom(p)

    def pick_out(self):
        p = QFileDialog.getExistingDirectory(self, "Pasta de saída", self.ed_out.text() or str(Path.home()))
        if p:
            self.ed_out.setText(p)

    def _set_rom(self, p):
        self.ed_rom.setText(p)
        if extract.is_gba(p):
            self.cb_game.setCurrentIndex(0)
            self.load()
            return
        g = extract.guess_cps1_game(p)
        if g:
            self.cb_game.setCurrentText(g)
            self.load()
        else:
            self.lb_info.setText("Escolha o jogo CPS1 na lista e clique em Carregar.")

    def load(self):
        rom = self.ed_rom.text().strip()
        if not rom:
            QMessageBox.information(self, "Extração", "Escolha primeiro a ROM (.zip, .gba ou pasta).")
            return
        try:
            self.game = extract.open_rom(rom, self.cb_game.currentText().strip() or None)
        except Exception as e:  # noqa: BLE001
            self.game = None
            QMessageBox.critical(self, "Erro ao carregar", str(e))
            return
        self.qs.setValue("extract/rom", rom)
        gba = extract.platform(self.game) == "GBA"
        if not gba:
            self.cb_game.setCurrentText(self.game.name)
        self.ls_songs.clear()
        self.song_ids = []
        for sid, _ptr, n in self.game.songs():
            kind = f"{n} faixas" if n > 1 else "1 faixa"
            self.ls_songs.addItem(f"{extract.song_label(self.game, sid)}  ({kind})")
            self.song_ids.append(sid)
        self.ls_songs.selectAll()
        for w in self._cps1_only:
            w.setEnabled(not gba)
        self.lb_info.setText(extract.summary(self.game))
        self.log.clear()
        for w in self.game.warnings:
            self.log.appendPlainText("Aviso: " + w)

    def run(self):
        if self.job is not None:
            return
        if not self.game:
            self.load()
            if not self.game:
                return
        sel = sorted(self.song_ids[i.row()] for i in self.ls_songs.selectedIndexes())
        if self.ck_midi.isChecked() and not sel:
            QMessageBox.information(self, "Extração", "Selecione ao menos uma música.")
            return
        out = self.ed_out.text().strip() or str(Path.home() / "Desktop" / "Audio_extraido")
        self.qs.setValue("extract/out", out)
        opts = dict(plays=self.sp_plays.value(), do_midi=self.ck_midi.isChecked(),
                    do_sf2=self.ck_sf2.isChecked(), do_wav=self.ck_wav.isChecked(),
                    do_opm=self.ck_opm.isChecked(), do_mdlib=self.ck_lib.isChecked(),
                    quality="full" if self.rb_full.isChecked() else "quick",
                    song_ids=set(sel) if len(sel) < len(self.song_ids) else None)
        self.log.clear()
        self.prog.setValue(0)
        self.bt_go.setEnabled(False)
        self.job = _Job(self.game, out, opts)
        self.job.signals.log.connect(self.log.appendPlainText)
        self.job.signals.prog.connect(lambda a, b: (self.prog.setMaximum(b), self.prog.setValue(a)))
        self.job.signals.done.connect(self._done)
        self.pool.start(self.job)

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
        for m in o.midis:
            self.ls_out.addItem(os.path.basename(m))
        if o.midis:
            self.ls_out.setCurrentRow(0)
            self.bt_send.setEnabled(bool(self.open_in_converter))
        info = f"{len(o.midis)} MIDI(s) em {base}"
        info += f"\nSoundFont: {os.path.basename(o.sf2)}" if o.sf2 else "\nSem SoundFont: marque “SoundFont (.sf2)”."
        if o.library:
            info += "\nBiblioteca de patches FM do jogo: vai junto para a conversão."
        self.lb_out.setText(info)

    def send_to_converter(self):
        o = getattr(self, "_outs", None)
        row = self.ls_out.currentRow()
        if not o or row < 0 or row >= len(o.midis) or not self.open_in_converter:
            return
        if not o.sf2:
            QMessageBox.information(self, "Extração", "Gere também o SoundFont (.sf2) para converter.")
            return
        self.open_in_converter(o.midis[row], o.sf2, o.library)

    def open_folder(self):
        if self.last_base and os.path.isdir(self.last_base):
            from mdconv.gui.main_window import open_with_system
            open_with_system(Path(self.last_base))

    def busy(self) -> bool:
        return self.job is not None
