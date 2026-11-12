"""Janela principal (PySide6). Toda a lógica está em :mod:`mdconv.app.project`; aqui só há
widgets, a thread de trabalho e o player de áudio."""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from mdconv.app.project import PROJECT_SUFFIX, Cancelled, Project, ProjectSettings

APP_NAME = "Mega Drive Audio Converter"

try:  # o player é opcional: sem QtMultimedia, os WAVs abrem no player do sistema
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    HAVE_PLAYER = True
except ImportError:  # pragma: no cover
    HAVE_PLAYER = False


# ------------------------------------------------------------------ thread de trabalho
class _Signals(QObject):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """Roda ``fn(progress=...)`` fora da interface. Cancelar = o próximo aviso de progresso
    interrompe o trabalho (ajustes FM avisam a cada etapa)."""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.cancelled = False
        self.setAutoDelete(False)

    def run(self):
        def progress(msg: str):
            if self.cancelled:
                raise Cancelled()
            self.signals.progress.emit(str(msg))
        try:
            result = self.fn(*self.args, progress=progress, **self.kwargs)
        except Cancelled:
            self.signals.failed.emit("Cancelado.")
        except Exception as e:  # noqa: BLE001 — qualquer erro vira mensagem na interface
            detail = "".join(traceback.format_exception_only(type(e), e)).strip()
            self.signals.failed.emit(f"{detail}\n\n{traceback.format_exc()}")
        else:
            self.signals.done.emit(result)


def open_with_system(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _path_row(edit: QLineEdit, button: QPushButton) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    h.addWidget(button)
    return w


# ------------------------------------------------------------------ janela
class MainWindow(QMainWindow):
    COLS = ["Instrumento", "Canais", "Notas", "Função", "Destino", "Canal no chip", "Patch FM"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 780)
        self.qsettings = QSettings("mdconv", APP_NAME)
        self.project: Project | None = None
        self.project_path: Path | None = None
        self.pool = QThreadPool.globalInstance()
        self.task: Task | None = None
        self._on_done = None
        self._busy_widgets: list[QWidget] = []
        self._rows = []
        self._preview_path: Path | None = None

        if HAVE_PLAYER:
            self.player = QMediaPlayer(self)
            self.audio_out = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_out)
        else:
            self.player = None

        self._build_menu()
        central = QWidget()
        v = QVBoxLayout(central)
        v.addWidget(self._build_files())
        split = QSplitter(Qt.Vertical)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_instruments_tab(), "1. Instrumentos")
        self.tabs.addTab(self._build_arrangement_tab(), "2. Arranjo")
        self.tabs.addTab(self._build_loop_tab(), "3. Loop")
        self.tabs.addTab(self._build_export_tab(), "4. Prévia e exportação")
        split.addWidget(self.tabs)
        split.addWidget(self._build_log())
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 1)
        self.setCentralWidget(central)
        self._settings_to_widgets(ProjectSettings(
            library=self.qsettings.value("library", str(Path.home() / "mdconv" / "biblioteca_patches")),
            bank=self.qsettings.value("bank", ""),
            out_dir=self.qsettings.value("out_dir", str(Path.home() / "mdconv" / "saida"))))
        self._set_loaded(False)

    # ------------------------------------------------------------- construção
    def _build_menu(self):
        m = self.menuBar().addMenu("&Arquivo")
        for text, key, slot in (("Novo projeto", QKeySequence.New, self.new_project),
                                ("Abrir projeto…", QKeySequence.Open, self.open_project),
                                ("Salvar projeto", QKeySequence.Save, self.save_project),
                                ("Salvar projeto como…", QKeySequence.SaveAs, self.save_project_as)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        t = QAction("Montar banco de patches .tfi…", self)
        t.triggered.connect(self.build_tfi_bank)
        m.addAction(t)
        m.addSeparator()
        q = QAction("Sair", self)
        q.triggered.connect(self.close)
        m.addAction(q)
        h = self.menuBar().addMenu("A&juda")
        a = QAction("Sobre", self)
        a.triggered.connect(self.about)
        h.addAction(a)

    def _build_files(self) -> QWidget:
        g = QGroupBox("Arquivos")
        f = QFormLayout(g)
        self.ed_midi, self.ed_sf2 = QLineEdit(), QLineEdit()
        self.ed_library, self.ed_out = QLineEdit(), QLineEdit()
        self.ed_bank = QLineEdit()
        for ed, label, kind, filt in (
                (self.ed_midi, "MIDI (GBA Mus Ripper):", "file", "MIDI (*.mid *.midi)"),
                (self.ed_sf2, "SoundFont (.sf2):", "file", "SoundFont (*.sf2)"),
                (self.ed_library, "Biblioteca de patches:", "dir", None),
                (self.ed_bank, "Banco de patches prontos:", "dir", None),
                (self.ed_out, "Pasta de saída:", "dir", None)):
            b = QPushButton("Procurar…")
            b.clicked.connect(lambda _=False, e=ed, k=kind, fl=filt: self._browse(e, k, fl))
            f.addRow(label, _path_row(ed, b))
        self.ed_bank.setToolTip("Pasta criada por 'import-tfi' (patches .tfi de jogos e packs). "
                                "Opcional: dá candidatos extras a partir de patches prontos.")
        self.ed_library.setToolTip("Patches aprovados ficam aqui e servem para todas as músicas "
                                   "que usam o mesmo instrumento do SF2.")
        self.bt_load = QPushButton("Abrir música")
        self.bt_load.setDefault(True)
        self.bt_load.clicked.connect(self.load_song)
        f.addRow("", self.bt_load)
        return g

    def _build_instruments_tab(self) -> QWidget:
        w = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(self.COLS)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._instrument_selected)
        w.addWidget(self.table)

        side = QWidget()
        v = QVBoxLayout(side)
        self.lb_inst = QLabel("Selecione um instrumento.")
        self.lb_inst.setWordWrap(True)
        f = self.lb_inst.font()
        f.setBold(True)
        self.lb_inst.setFont(f)
        v.addWidget(self.lb_inst)
        g = QGroupBox("Gerar candidatos FM")
        fl = QFormLayout(g)
        self.cb_budget = QComboBox()
        for text, val in (("Rápido (~30 s a 2 min)", "quick"), ("Normal", "normal"), ("Completo (lento)", "thorough")):
            self.cb_budget.addItem(text, val)
        self.cb_budget.setCurrentIndex(1)
        self.sp_fb = QSpinBox()
        self.sp_fb.setRange(0, 7)
        self.sp_fb.setValue(5)
        self.sp_fb.setToolTip("Feedback 6–7 costuma gerar chiado no fim das notas.")
        self.ck_detune = QCheckBox("Liberar detune (coro — bom para strings/ensembles)")
        self.ck_bank = QCheckBox("Incluir candidatos do banco de patches prontos")
        self.ck_bank.setChecked(True)
        self.ck_bank_only = QCheckBox("Só do banco (bem mais rápido)")
        self.bt_fit = QPushButton("Gerar candidatos")
        self.bt_fit.clicked.connect(self.fit_selected)
        fl.addRow("Busca:", self.cb_budget)
        fl.addRow("Feedback máximo:", self.sp_fb)
        fl.addRow(self.ck_detune)
        fl.addRow(self.ck_bank)
        fl.addRow(self.ck_bank_only)
        fl.addRow(self.bt_fit)
        v.addWidget(g)
        v.addWidget(QLabel("Candidatos (em cada WAV: original, depois FM, nota por nota):"))
        self.ls_cand = QListWidget()
        self.ls_cand.itemDoubleClicked.connect(lambda _: self.play_candidate())
        v.addWidget(self.ls_cand, 1)
        h = QHBoxLayout()
        self.bt_play_cand = QPushButton("▶ Ouvir A/B")
        self.bt_play_cand.clicked.connect(self.play_candidate)
        self.bt_approve = QPushButton("✔ Aprovar escolhido")
        self.bt_approve.clicked.connect(self.approve_candidate)
        h.addWidget(self.bt_play_cand)
        h.addWidget(self.bt_approve)
        v.addLayout(h)
        w.addWidget(side)
        w.setStretchFactor(0, 3)
        w.setStretchFactor(1, 2)
        self._busy_widgets += [self.bt_fit, self.bt_approve]
        return w

    def _build_arrangement_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("Opções")
        fl = QFormLayout(g)
        self.ck_pcm = QCheckBox("Bateria em PCM (o canal FM 6 vira DAC)")
        pcm_row = QWidget()
        h = QHBoxLayout(pcm_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.ck_pcm_ch = []
        for c in (1, 2, 3, 4):
            ck = QCheckBox(f"CH{c}")
            self.ck_pcm_ch.append(ck)
            h.addWidget(ck)
        h.addWidget(QLabel("  (os canais não marcados ficam livres para os efeitos do jogo)"))
        h.addStretch(1)
        self.ck_vibrato = QCheckBox("Vibrato (modulação do m4a)")
        self.cb_alloc = QComboBox()
        for label, data in (("Automático (o melhor dos dois)", "auto"),
                            ("Fixos por parte (como no tracker)", "fixo"),
                            ("Nota a nota (dinâmico)", "dinamico")):
            self.cb_alloc.addItem(label, data)
        self.cb_alloc.setToolTip(
            "Fixos: as partes principais ganham canal FM próprio (sem recarregar patch, o release\n"
            "termina sob a nota seguinte); vozes a mais vão para o PSG.\n"
            "Nota a nota: qualquer canal livre, a cada nota (perde menos notas em músicas densas).\n"
            "Automático: converte dos dois jeitos e fica com o que perde menos notas e recarrega\n"
            "menos patches (o relatório mostra a comparação).")
        self.ck_grid = QCheckBox("Travar o andamento na grade de 60 Hz (ritmo sem oscilação; muda o andamento em até 6%)")
        self.ck_grid.setToolTip(
            "Ajusta o andamento para o passo rítmico durar um número inteiro de quadros, como um\n"
            "músico faria no tracker. Ex.: 102 → 100 BPM deixa a semicolcheia em 6 quadros exatos\n"
            "em vez de alternar 6 e 5.")
        self.ck_noise = QCheckBox("Chimbais e pratos no ruído do PSG (libera o PCM para bumbo e caixa)")
        self.ck_noise.setToolTip(
            "Peças metálicas do kit (detectadas pelo som: agudas e sem grave) tocam no canal de\n"
            "ruído do PSG, com o envelope da própria amostra e o volume de cada nota.\n"
            "Os canais PCM ficam para bumbo, caixa e tons, e a ROM gasta menos com amostras.")
        bal = QWidget()
        hb = QHBoxLayout(bal)
        hb.setContentsMargins(0, 0, 0, 0)
        self.ck_auto_bal = QCheckBox("Automático")
        self.sp_att = QDoubleSpinBox()
        self.sp_att.setRange(0, 30)
        self.sp_att.setSuffix(" dB")
        self.sp_att.setSingleStep(0.5)
        self.ck_auto_bal.toggled.connect(lambda on: self.sp_att.setEnabled(not on))
        hb.addWidget(self.ck_auto_bal)
        hb.addWidget(QLabel("  atenuação FM/PSG:"))
        hb.addWidget(self.sp_att)
        hb.addStretch(1)
        fl.addRow(self.ck_pcm)
        fl.addRow("Canais PCM da música:", pcm_row)
        fl.addRow("Canais FM:", self.cb_alloc)
        fl.addRow(self.ck_grid)
        fl.addRow(self.ck_noise)
        fl.addRow(self.ck_vibrato)
        fl.addRow("Equilíbrio bateria × resto:", bal)
        self.bt_replan = QPushButton("Aplicar e refazer o arranjo")
        self.bt_replan.clicked.connect(self.replan)
        fl.addRow(self.bt_replan)
        v.addWidget(g)
        self.tx_plan = QPlainTextEdit()
        self.tx_plan.setReadOnly(True)
        self.tx_plan.setFont(QFont("Consolas", 9))
        v.addWidget(self.tx_plan, 1)
        self._busy_widgets.append(self.bt_replan)
        return w

    def _build_loop_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        fl = QFormLayout()
        self.cb_loop = QComboBox()
        for text, val in (("Automático (marcadores; senão estima)", "auto"),
                          ("Só com marcador loopStart/loopEnd", "markers"),
                          ("Sem loop (toca uma vez)", "none")):
            self.cb_loop.addItem(text, val)
        self.cb_loop.currentIndexChanged.connect(self._loop_mode_changed)
        fl.addRow("Modo:", self.cb_loop)
        self.lb_loop = QLabel("—")
        self.lb_loop.setWordWrap(True)
        fl.addRow("Loop atual:", self.lb_loop)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.sp_bar = QSpinBox()
        self.sp_bar.setRange(1, 1)
        self.bt_bar = QPushButton("Usar este compasso")
        self.bt_bar.clicked.connect(self.set_loop_bar)
        self.bt_bar_auto = QPushButton("Voltar à detecção")
        self.bt_bar_auto.clicked.connect(lambda: self.set_loop_bar(auto=True))
        h.addWidget(self.sp_bar)
        h.addWidget(self.bt_bar)
        h.addWidget(self.bt_bar_auto)
        h.addStretch(1)
        fl.addRow("Início do loop no compasso:", row)
        v.addLayout(fl)
        self.bt_seam = QPushButton("▶ Ouvir a emenda (fim da música → volta do loop)")
        self.bt_seam.clicked.connect(self.play_seam)
        v.addWidget(self.bt_seam)
        tip = QLabel("Sem marcador no MIDI, a volta é estimada pelo jeito que a música termina. "
                     "Ouça a emenda: se a música voltar no lugar errado, escolha o compasso certo.")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        self._busy_widgets += [self.bt_seam, self.bt_bar, self.bt_bar_auto]
        return w

    def _build_export_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        self.bt_preview = QPushButton("Gerar prévia")
        self.bt_preview.clicked.connect(self.make_preview)
        self.bt_play = QPushButton("▶ Tocar")
        self.bt_play.clicked.connect(lambda: self._play(self._preview_path))
        self.bt_stop = QPushButton("■ Parar")
        self.bt_stop.clicked.connect(self.stop_audio)
        for b in (self.bt_preview, self.bt_play, self.bt_stop):
            h.addWidget(b)
        h.addWidget(QLabel("   Emulador do FM:"))
        self.cb_engine = QComboBox()
        self.cb_engine.addItem("Nuked-OPN2 (modo YM2612, mais fiel)", "auto")
        self.cb_engine.addItem("Emulador próprio (mais rápido)", "proprio")
        self.cb_engine.setToolTip("Só muda a prévia; os arquivos XGM/VGM são os mesmos.")
        h.addWidget(self.cb_engine)
        h.addStretch(1)
        v.addLayout(h)
        g = QGroupBox("Exportação")
        fl = QFormLayout(g)
        fr = QWidget()
        hf = QHBoxLayout(fr)
        hf.setContentsMargins(0, 0, 0, 0)
        self.ck_xgm = QCheckBox("XGM v1 (SGDK)")
        self.ck_vgm = QCheckBox("VGM 1.71 (players/emuladores)")
        self.ck_fur = QCheckBox("Furnace 0.6.8.3 (.fur)")
        self.ck_fur.setToolTip("Projeto do Furnace com as mesmas notas, instrumentos e bateria, para editar.")
        hf.addWidget(self.ck_xgm)
        hf.addWidget(self.ck_vgm)
        hf.addWidget(self.ck_fur)
        hf.addStretch(1)
        self.ed_name, self.ed_title, self.ed_game = QLineEdit(), QLineEdit(), QLineEdit()
        self.ed_name.setPlaceholderText("padrão: nome do MIDI")
        fl.addRow("Formatos:", fr)
        fl.addRow("Nome dos arquivos:", self.ed_name)
        fl.addRow("Título (VGM):", self.ed_title)
        fl.addRow("Jogo (VGM):", self.ed_game)
        hb = QHBoxLayout()
        self.bt_export = QPushButton("Exportar")
        self.bt_export.clicked.connect(self.export)
        self.bt_open_out = QPushButton("Abrir pasta de saída")
        self.bt_open_out.clicked.connect(self.open_out_dir)
        hb.addWidget(self.bt_export)
        hb.addWidget(self.bt_open_out)
        hb.addStretch(1)
        fl.addRow(hb)
        v.addWidget(g)
        self.tx_export = QPlainTextEdit()
        self.tx_export.setReadOnly(True)
        self.tx_export.setFont(QFont("Consolas", 9))
        v.addWidget(self.tx_export, 1)
        self._busy_widgets += [self.bt_preview, self.bt_export]
        return w

    def _build_log(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.bt_cancel = QPushButton("Cancelar")
        self.bt_cancel.setEnabled(False)
        self.bt_cancel.clicked.connect(self.cancel_task)
        h.addWidget(self.progress, 1)
        h.addWidget(self.bt_cancel)
        v.addLayout(h)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFont(QFont("Consolas", 9))
        v.addWidget(self.log, 1)
        return w

    # ------------------------------------------------------------- configurações ↔ widgets
    def _settings_to_widgets(self, s: ProjectSettings):
        self.ed_midi.setText(s.midi)
        self.ed_sf2.setText(s.sf2)
        self.ed_library.setText(s.library)
        self.ed_bank.setText(s.bank)
        self.ed_out.setText(s.out_dir)
        self.ck_pcm.setChecked(s.use_pcm)
        for c, ck in enumerate(self.ck_pcm_ch, 1):
            ck.setChecked(c in s.pcm_channels)
        self.ck_vibrato.setChecked(s.vibrato)
        self.ck_noise.setChecked(s.noise_metals)
        self.cb_alloc.setCurrentIndex(max(0, self.cb_alloc.findData(s.fm_alloc)))
        self.ck_grid.setChecked(s.snap_tempo)
        self.ck_auto_bal.setChecked(s.fm_att_db is None)
        self.sp_att.setValue(s.fm_att_db or 0.0)
        self.sp_att.setEnabled(s.fm_att_db is not None)
        self.cb_loop.blockSignals(True)
        self.cb_loop.setCurrentIndex(max(0, self.cb_loop.findData(s.loop_mode)))
        self.cb_loop.blockSignals(False)
        self.cb_engine.setCurrentIndex(max(0, self.cb_engine.findData(
            s.fm_engine if s.fm_engine in ("auto", "proprio") else "auto")))
        self.ck_xgm.setChecked("xgm" in s.formats)
        self.ck_vgm.setChecked("vgm" in s.formats)
        self.ck_fur.setChecked("fur" in s.formats)
        self.ed_name.setText(s.name)
        self.ed_title.setText(s.title)
        self.ed_game.setText(s.game)

    def _widgets_to_settings(self, s: ProjectSettings | None = None) -> ProjectSettings:
        s = s or ProjectSettings()
        s.midi, s.sf2 = self.ed_midi.text().strip(), self.ed_sf2.text().strip()
        s.library, s.out_dir = self.ed_library.text().strip(), self.ed_out.text().strip()
        s.bank = self.ed_bank.text().strip()
        s.use_pcm = self.ck_pcm.isChecked()
        s.pcm_channels = [c for c, ck in enumerate(self.ck_pcm_ch, 1) if ck.isChecked()]
        s.vibrato = self.ck_vibrato.isChecked()
        s.noise_metals = self.ck_noise.isChecked()
        s.fm_alloc = self.cb_alloc.currentData()
        s.snap_tempo = self.ck_grid.isChecked()
        s.fm_att_db = None if self.ck_auto_bal.isChecked() else float(self.sp_att.value())
        s.loop_mode = self.cb_loop.currentData()
        s.formats = [f for f, ck in (("xgm", self.ck_xgm), ("vgm", self.ck_vgm), ("fur", self.ck_fur))
                     if ck.isChecked()]
        s.fm_engine = self.cb_engine.currentData()
        s.name, s.title, s.game = (self.ed_name.text().strip(), self.ed_title.text().strip(),
                                   self.ed_game.text().strip())
        return s

    def _sync_settings(self):
        """Leva as opções da tela para o projeto aberto (sem mexer no loop manual)."""
        if self.project:
            self._widgets_to_settings(self.project.settings)
            self.project.invalidate()

    # ------------------------------------------------------------- execução em segundo plano
    def _run(self, title: str, fn, on_done, *args, **kwargs):
        if self.task is not None:
            QMessageBox.information(self, APP_NAME, "Aguarde a tarefa atual terminar (ou cancele).")
            return
        self.task = Task(fn, *args, **kwargs)
        self._on_done = on_done
        self.task.signals.progress.connect(self._task_progress)
        self.task.signals.done.connect(self._task_done)
        self.task.signals.failed.connect(self._task_failed)
        self._log(f"▶ {title}")
        self._set_busy(True)
        self.pool.start(self.task)

    def _task_progress(self, msg: str):
        self._log(msg)

    def _task_done(self, result):
        cb, self._on_done, self.task = self._on_done, None, None
        self._set_busy(False)
        if cb:
            try:
                cb(result)
            except Exception as e:  # noqa: BLE001
                self._error(f"{type(e).__name__}: {e}", traceback.format_exc())

    def _task_failed(self, msg: str):
        self._on_done, self.task = None, None
        self._set_busy(False)
        if msg == "Cancelado.":
            self._log("■ Cancelado.")
            return
        first, _, detail = msg.partition("\n\n")
        self._error(first, detail)

    def cancel_task(self):
        if self.task:
            self.task.cancelled = True
            self._log("Cancelando no próximo passo…")

    def _set_busy(self, busy: bool):
        self.progress.setRange(0, 0 if busy else 1)
        self.bt_cancel.setEnabled(busy)
        self.bt_load.setEnabled(not busy)
        for wdg in self._busy_widgets:
            wdg.setEnabled(not busy and self.project is not None and self.project.song is not None)

    def _set_loaded(self, loaded: bool):
        for wdg in self._busy_widgets + [self.bt_play_cand, self.bt_play, self.bt_open_out]:
            wdg.setEnabled(loaded)

    def _log(self, msg: str):
        self.log.appendPlainText(msg)

    def _error(self, msg: str, detail: str = ""):
        self._log(f"✖ {msg}")
        box = QMessageBox(QMessageBox.Warning, APP_NAME, msg, parent=self)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    # ------------------------------------------------------------- arquivos e projeto
    def _browse(self, edit: QLineEdit, kind: str, filt: str | None):
        start = edit.text() or str(Path.home())
        if kind == "dir":
            p = QFileDialog.getExistingDirectory(self, "Escolha a pasta", start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Escolha o arquivo", start, filt or "")
        if p:
            edit.setText(p)

    def new_project(self):
        self.stop_audio()
        self.project, self.project_path = None, None
        s = ProjectSettings(library=self.ed_library.text(), out_dir=self.ed_out.text())
        self._settings_to_widgets(s)
        self.table.setRowCount(0)
        self.ls_cand.clear()
        self.tx_plan.clear()
        self.tx_export.clear()
        self.lb_loop.setText("—")
        self._set_loaded(False)
        self.setWindowTitle(APP_NAME)

    def open_project(self):
        p, _ = QFileDialog.getOpenFileName(self, "Abrir projeto", str(Path.home()),
                                           f"Projeto ({'*' + PROJECT_SUFFIX})")
        if not p:
            return
        try:
            s = ProjectSettings.load(p)
        except (OSError, ValueError, TypeError) as e:
            self._error(f"Não consegui ler o projeto: {e}")
            return
        self.project_path = Path(p)
        self._settings_to_widgets(s)
        self._load(s)

    def save_project(self):
        if self.project_path is None:
            return self.save_project_as()
        s = self.project.settings if self.project else ProjectSettings()
        self._widgets_to_settings(s)
        self.project_path = s.save(self.project_path)
        self._log(f"Projeto salvo: {self.project_path}")

    def save_project_as(self):
        start = str(Path(self.ed_out.text() or Path.home()) / (Path(self.ed_midi.text()).stem + PROJECT_SUFFIX))
        p, _ = QFileDialog.getSaveFileName(self, "Salvar projeto", start, f"Projeto ({'*' + PROJECT_SUFFIX})")
        if p:
            self.project_path = Path(p)
            self.save_project()

    def load_song(self):
        s = self._widgets_to_settings()
        if self.project and self.project.settings.midi == s.midi:
            s.loop_start_tick = self.project.settings.loop_start_tick
            s.loop_end_tick = self.project.settings.loop_end_tick
        self._load(s)

    def _load(self, s: ProjectSettings):
        errs = [e for e in s.validate() if "MIDI" in e or "SF2" in e]
        if errs:
            self._error("Antes de abrir: " + "; ".join(errs) + ".")
            return
        self.qsettings.setValue("library", s.library)
        self.qsettings.setValue("bank", s.bank)
        self.qsettings.setValue("out_dir", s.out_dir)
        self.stop_audio()
        project = Project(s)

        def work(progress):
            warnings = project.load(progress)
            return warnings, project.instruments(), project.loop()
        self._run(f"Abrindo {Path(s.midi).name}", work, lambda r: self._loaded(project, *r))

    def _loaded(self, project: Project, warnings, rows, loop):
        self.project = project
        self.setWindowTitle(f"{APP_NAME} — {Path(project.settings.midi).name}")
        for w in warnings:
            self._log(f"⚠ {w}")
        self._fill_instruments(rows)
        self.tx_plan.setPlainText(project.plan_text())
        self.sp_bar.setRange(1, project.bar_count())
        self._show_loop(loop)
        self._set_loaded(True)
        self._set_busy(False)
        n_prov = sum(1 for r in rows if r.status == "provisório")
        self._log(f"✔ {len(rows)} instrumentos; {n_prov} ainda sem patch FM aprovado.")

    # ------------------------------------------------------------- instrumentos
    def _fill_instruments(self, rows):
        sel = self._selected_row()
        key = sel.key if sel else None
        self._rows = rows
        self.table.blockSignals(True)            # sem reações no meio do preenchimento
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r.label, ", ".join(f"C{c}" for c in r.channels), str(r.notes), ", ".join(r.roles),
                    " + ".join(r.pools), r.chip_text() or "—",
                    r.status + (f" ({len(r.candidates)} cand.)"
                                if r.candidates and r.status != "aprovado" else "")]
            for j, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if j == 5:
                    it.setToolTip(self._chip_tooltip(r))
                if j == 6 and r.status == "provisório":
                    it.setForeground(Qt.darkYellow)
                elif j == 6 and r.status == "aprovado":
                    it.setForeground(Qt.darkGreen)
                self.table.setItem(i, j, it)
        target = next((i for i, r in enumerate(rows) if r.key == key), 0 if rows else -1)
        if target >= 0:
            self.table.setCurrentCell(target, 0)
            self.table.selectRow(target)
        self.table.blockSignals(False)
        self._instrument_selected()

    @staticmethod
    def _chip_tooltip(r) -> str:
        if not r.chip_use:
            return ("Aparece depois de converter (aba Arranjo → \"Aplicar e refazer o arranjo\" ou prévia).\n"
                    "Informativo: o driver escolhe o canal nota a nota.")
        lines = [f"{k}: {v} nota(s)" for k, v in sorted(r.chip_use.items(), key=lambda kv: -kv[1])]
        return ("Canais do chip usados na última conversão (o driver escolhe nota a nota):\n"
                + "\n".join(lines))

    def _selected_row(self):
        # Pela SELEÇÃO, não por currentRow(): ao clicar, o Qt avisa a mudança de seleção
        # antes de atualizar a linha "corrente", e currentRow() ainda apontava para o
        # instrumento anterior — os candidatos mostrados e o "Aprovar" iam para o errado.
        sm = self.table.selectionModel()
        rows = sm.selectedRows() if sm is not None else []
        idx = rows[0].row() if rows else self.table.currentRow()
        return self._rows[idx] if 0 <= idx < len(self._rows) else None

    def _instrument_selected(self):
        r = self._selected_row()
        self.ls_cand.clear()
        if r is None:
            self.lb_inst.setText("Selecione um instrumento.")
            return
        info = f"{r.label} — canais {', '.join(map(str, r.channels))} — {r.status}"
        if r.chip_use:
            info += f"\nNo chip: {r.chip_text()}"
        if not r.needs_fm:
            info += ("\nEste instrumento não toca no FM (" + " + ".join(r.pools) +
                     "), não precisa de patch.")
        self.lb_inst.setText(info)
        self.bt_fit.setEnabled(r.needs_fm and self.task is None)
        current = 0
        for i, c in enumerate(r.candidates):
            it = QListWidgetItem(("✔ APROVADO  " if c.approved else "") + f"#{c.rank}  {c.summary}")
            it.setData(Qt.UserRole, c)
            if c.approved:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                current = i
            self.ls_cand.addItem(it)
        if r.candidates:
            self.ls_cand.setCurrentRow(current)

    def fit_selected(self):
        r = self._selected_row()
        if not (self.project and r):
            return
        self.stop_audio()            # libera os WAVs antigos (o Windows trava arquivo aberto)
        budget, fb, det = self.cb_budget.currentData(), self.sp_fb.value(), self.ck_detune.isChecked()
        use_bank, bank_only = self.ck_bank.isChecked(), self.ck_bank_only.isChecked()
        self._sync_settings()
        project = self.project

        def work(progress):
            project.fit(r.key, budget=budget, max_fb=fb, detune=det, progress=progress,
                        use_bank=use_bank, bank_only=bank_only and use_bank)
            return project.instruments()
        self._run(f"Gerando candidatos FM para {r.label}", work, self._after_instruments)

    def _after_instruments(self, rows):
        self._fill_instruments(rows)
        self._log("✔ Pronto. Ouça os candidatos e aprove o que soar melhor.")

    def _after_approve(self, result):
        info, rows = result
        self._fill_instruments(rows)
        self._log(f"✔ Aprovado: {info['label']} candidato #{info['rank']} → "
                  f"{self.project.library_dir / (info['library_id'] + '.json')}")
        r = next((x for x in rows if x.label == info["row_label"]), None)
        if r is not None and r.status != "aprovado":
            self._error("O patch foi gravado, mas o instrumento continua sem patch aprovado na tabela. "
                        "Confira se a pasta da biblioteca é a mesma usada na conversão e me mande o "
                        "texto do registro abaixo.")

    def _selected_candidate(self):
        it = self.ls_cand.currentItem()
        return it.data(Qt.UserRole) if it else None

    def play_candidate(self):
        c = self._selected_candidate()
        if c:
            self._play(c.wav)

    def approve_candidate(self):
        r, c = self._selected_row(), self._selected_candidate()
        if not self.project:
            return
        if r is None:
            self._error("Selecione o instrumento na tabela.")
            return
        if c is None:
            self._error("Selecione um candidato na lista (gere os candidatos antes, se a lista estiver vazia).")
            return
        project = self.project

        def work(progress):
            progress(f"Aprovando #{c.rank} de {r.label}…")
            info = project.approve(r.key, c.rank)
            info["row_label"] = r.label
            return info, project.instruments()
        self._run("Aprovando", work, self._after_approve)

    # ------------------------------------------------------------- arranjo
    def replan(self):
        if not self.project:
            return
        self._sync_settings()
        errs = self.project.settings.validate()
        if errs:
            self._error("; ".join(errs))
            return
        project = self.project

        def work(progress):
            progress("Refazendo o arranjo…")
            project.replan()
            c = project.convert(progress)
            return project.instruments(), project.report(), c
        self._run("Arranjo", work, self._after_replan)

    def _after_replan(self, result):
        rows, report, c = result
        self._fill_instruments(rows)
        self.tx_plan.setPlainText(report)
        if c.balance:
            self._log(f"Equilíbrio automático: FM/PSG −{c.balance['fm_psg_att_db']} dB.")
        self._show_loop(c.loop)

    # ------------------------------------------------------------- loop
    def _show_loop(self, loop):
        if loop is None:
            self.lb_loop.setText("sem loop — a música toca uma vez e para.")
            return
        bar = self.project.tick_to_bar(loop.start_tick) if self.project else 0
        txt = (f"de {loop.start_s:.2f} s (compasso {bar:.0f}) até {loop.end_s:.2f} s — "
               f"origem: {loop.source}. {loop.detail}")
        if loop.estimated:
            txt = "⚠ ESTIMADO — " + txt
        self.lb_loop.setText(txt)
        self.sp_bar.setValue(int(round(bar)))

    def _loop_mode_changed(self):
        if self.project:
            self.project.settings.loop_mode = self.cb_loop.currentData()
            self.project.invalidate()
            try:
                self._show_loop(self.project.loop())
            except ValueError as e:
                self._error(str(e))

    def set_loop_bar(self, auto: bool = False):
        if not self.project:
            return
        try:
            self.project.set_loop_start_bar(None if auto else self.sp_bar.value())
            self._show_loop(self.project.loop())
        except ValueError as e:
            self._error(str(e))

    def play_seam(self):
        if not self.project:
            return
        self._sync_settings()
        self.stop_audio()
        self._run("Emenda do loop", self.project.render_seam, self._play)

    # ------------------------------------------------------------- prévia / exportação
    def make_preview(self):
        if not self.project:
            return
        self._sync_settings()
        self.stop_audio()

        def done(path):
            self._preview_path = path
            self._log(f"✔ Prévia: {path}")
            self._play(path)
        self._run("Prévia da música", self.project.render_preview, done)

    def export(self):
        if not self.project:
            return
        self._sync_settings()
        errs = self.project.settings.validate()
        if not self.project.settings.formats:
            errs.append("marque ao menos um formato (XGM, VGM ou Furnace)")
        if errs:
            self._error("; ".join(errs))
            return
        self.stop_audio()

        def done(lines):
            self.tx_export.setPlainText("\n".join(lines))
            self._log("✔ Exportado em " + str(self.project.out_dir))
        self._run("Exportação", self.project.export, done)

    def open_out_dir(self):
        d = Path(self.ed_out.text())
        d.mkdir(parents=True, exist_ok=True)
        open_with_system(d)

    # ------------------------------------------------------------- áudio
    def _play(self, path):
        if not path:
            return
        path = Path(path)
        if not path.exists():
            self._error(f"Arquivo não encontrado: {path}")
            return
        if self.player is None:
            open_with_system(path)
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self.player.play()
        self._log(f"♪ {path.name}")

    def stop_audio(self):
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())

    # ------------------------------------------------------------- banco .tfi
    def build_tfi_bank(self):
        src = QFileDialog.getExistingDirectory(self, "Pasta com os arquivos .tfi (subpastas incluídas)",
                                               str(Path.home()))
        if not src:
            return
        dst = QFileDialog.getExistingDirectory(self, "Pasta onde gravar o banco (de preferência vazia)",
                                               str(Path(self.ed_library.text() or Path.home()).parent))
        if not dst:
            return
        from mdconv.library.bank import build_bank
        workers = max(1, (os.cpu_count() or 2) - 1)

        def work(progress):
            return build_bank(src, dst, workers=workers, progress=progress)

        def done(s):
            self.ed_bank.setText(dst)
            o = s["operator_order_check"]
            msg = (f"Banco pronto: {s['in_bank']} patches diferentes de {s['files']} arquivos "
                   f"({s['n_errors']} com defeito; {s['ssg_eg_ignored']} usam SSG-EG, importados sem ele). "
                   f"Ordem dos operadores conferida em {o['alg4']} patches de algoritmo 4.")
            self._log("✔ " + msg)
            QMessageBox.information(self, APP_NAME, msg + "\n\nO campo 'Banco de patches prontos' já aponta "
                                    "para ele.")
        self._run("Montando o banco de patches .tfi (milhares de patches levam alguns minutos)", work, done)

    # ------------------------------------------------------------- outros
    def about(self):
        QMessageBox.about(self, APP_NAME,
                          f"<b>{APP_NAME}</b><br>Converte músicas de GBA (MIDI + SF2) para o som do "
                          "Mega Drive: YM2612 (FM), SN76489 (PSG) e PCM, exportando XGM v1 (SGDK) e VGM."
                          "<br><br>Software livre."
                          "<br><br>Prévia do FM com o <b>Nuked-OPN2</b> © Alexey Khokholov (Nuke.YKT), "
                          "licença GNU LGPL 2.1 ou posterior — código-fonte e licença na pasta "
                          "<code>mdconv/chips/native/nuked_opn2</code>.")

    def closeEvent(self, ev):  # noqa: N802 (nome do Qt)
        if self.task is not None:
            if QMessageBox.question(self, APP_NAME, "Há uma tarefa em andamento. Sair mesmo assim?") \
                    != QMessageBox.Yes:
                ev.ignore()
                return
            self.task.cancelled = True
        self.stop_audio()
        ev.accept()


def main(argv=None) -> int:
    import multiprocessing
    multiprocessing.freeze_support()     # o banco .tfi usa processos paralelos (também no .exe)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    w = MainWindow()
    w.show()
    return app.exec()
