"""Janela principal (PySide6). Toda a lógica está em :mod:`mdconv.app.project`; aqui só há
widgets, a thread de trabalho e o player de áudio."""
from __future__ import annotations

from mdconv.i18n import tr
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

from mdconv import __version__
from mdconv.extract import __version__ as extract_version

APP_NAME = "Mega Drive Audio Converter"   # também o nome das configurações: não traduzir
APP_TITLE = f"{APP_NAME} {__version__}"

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
            self.signals.failed.emit(tr("Cancelado."))
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
    COLS = [tr("Instrumento"), tr("Canais"), tr("Notas"), tr("Função"), tr("Destino"), tr("Canal no chip"), tr("Patch FM")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
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
        from mdconv.gui.extract_tab import ExtractTab
        self.extract_tab = ExtractTab(open_in_converter=self.open_from_extraction)
        central = QWidget()
        v = QVBoxLayout(central)
        v.addWidget(self._build_files())
        split = QSplitter(Qt.Vertical)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_instruments_tab(), tr("1. Instrumentos"))
        self.tabs.addTab(self._build_arrangement_tab(), tr("2. Arranjo"))
        self.tabs.addTab(self._build_loop_tab(), tr("3. Loop"))
        self.tabs.addTab(self._build_export_tab(), tr("4. Prévia e exportação"))
        split.addWidget(self.tabs)
        split.addWidget(self._build_log())
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 1)
        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(self.extract_tab, tr("Extração"))
        self.main_tabs.addTab(central, tr("Conversão"))
        self.main_tabs.setCurrentIndex(1)
        self.setCentralWidget(self.main_tabs)
        self._settings_to_widgets(ProjectSettings(
            library=self.qsettings.value("library", str(Path.home() / "mdconv" / "biblioteca_patches")),
            bank=self.qsettings.value("bank", ""),
            out_dir=self.qsettings.value("out_dir", str(Path.home() / "mdconv" / "saida"))))
        self._set_loaded(False)

    # ------------------------------------------------------------- construção
    def _build_menu(self):
        m = self.menuBar().addMenu(tr("&Arquivo"))
        for text, key, slot in ((tr("Novo projeto"), QKeySequence.New, self.new_project),
                                (tr("Abrir projeto…"), QKeySequence.Open, self.open_project),
                                (tr("Salvar projeto"), QKeySequence.Save, self.save_project),
                                (tr("Salvar projeto como…"), QKeySequence.SaveAs, self.save_project_as)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        u = QAction(tr("Atualizar patches travados pela biblioteca…"), self)
        u.triggered.connect(self.update_pinned)
        m.addAction(u)
        d = QAction(tr("Destravar patches desta música…"), self)
        d.triggered.connect(self.unpin_all)
        m.addAction(d)
        h = QAction(tr("Histórico de decisões…"), self)
        h.triggered.connect(self.show_decisions)
        m.addAction(h)
        m.addSeparator()
        q = QAction(tr("Sair"), self)
        q.triggered.connect(self.close)
        m.addAction(q)
        f = self.menuBar().addMenu(tr("&Ferramentas"))
        a = QAction(tr("Conversores e efeitos (VGM, efeitos de chip, WAV, banco .tfi)…"), self)
        a.setShortcut("Ctrl+T")
        a.triggered.connect(lambda: self.open_tools())
        f.addAction(a)
        from PySide6.QtGui import QActionGroup
        from mdconv.export import optimize
        size = f.addMenu(tr("Tamanho do XGM"))
        sgrp = QActionGroup(self)
        self.size_actions = {}
        for code, name in optimize.MODES.items():
            act = QAction(tr(name), self, checkable=True)
            self.size_actions[code] = act
            act.setChecked(code == optimize.mode())
            act.setToolTip(tr(optimize.HINTS[code]))
            act.triggered.connect(lambda _=False, c=code: self.set_size_mode(c))
            sgrp.addAction(act)
            size.addAction(act)
        size.setToolTipsVisible(True)
        h = self.menuBar().addMenu(tr("A&juda"))
        lang = h.addMenu(tr("Idioma / Language"))
        from PySide6.QtGui import QActionGroup
        from mdconv import i18n
        grp = QActionGroup(self)
        for code, name in i18n.LANGUAGES.items():
            act = QAction(name, self, checkable=True)
            act.setChecked(code == i18n.language())
            act.triggered.connect(lambda _=False, c=code: self.set_language(c))
            grp.addAction(act)
            lang.addAction(act)
        a = QAction(tr("Sobre"), self)
        a.triggered.connect(self.about)
        h.addAction(a)

    def set_size_mode(self, code: str) -> None:
        """Qualidade × tamanho do XGM/VGM gravado (vale para as próximas exportações)."""
        from mdconv.export import optimize
        optimize.set_mode(code)
        self.qsettings.setValue("export/size_mode", code)
        if code in getattr(self, "size_actions", {}):
            self.size_actions[code].setChecked(True)
        if getattr(self, "_tools", None) is not None:
            self._tools.sync_size_mode()
        self.statusBar().showMessage(tr("Tamanho do XGM: {0}").format(tr(optimize.MODES[code])), 5000)

    def set_language(self, code: str) -> None:
        """Grava o idioma escolhido; vale ao reabrir o programa (os textos são lidos quando a
        janela é montada). A mensagem sai nos dois idiomas."""
        from mdconv import i18n
        if code == i18n.language():
            return
        self.qsettings.setValue("ui/language", code)
        QMessageBox.information(
            self, "Idioma / Language",
            "O idioma muda quando o programa for aberto de novo.\n\n"
            "The language will change the next time you open the program.")

    def _build_files(self) -> QWidget:
        g = QGroupBox(tr("Arquivos"))
        f = QFormLayout(g)
        self.ed_midi, self.ed_sf2 = QLineEdit(), QLineEdit()
        self.ed_library, self.ed_out = QLineEdit(), QLineEdit()
        self.ed_bank = QLineEdit()
        for ed, label, kind, filt in (
                (self.ed_midi, "MIDI:", "file", tr("MIDI (*.mid *.midi)")),
                (self.ed_sf2, tr("SoundFont (.sf2):"), "file", tr("SoundFont (*.sf2)")),
                (self.ed_library, tr("Biblioteca de patches:"), "dir", None),
                (self.ed_bank, tr("Banco de patches prontos:"), "dir", None),
                (self.ed_out, tr("Pasta de saída:"), "dir", None)):
            b = QPushButton(tr("Procurar…"))
            b.clicked.connect(lambda _=False, e=ed, k=kind, fl=filt: self._browse(e, k, fl))
            f.addRow(label, _path_row(ed, b))
        self.ed_bank.setToolTip(tr("Pasta criada por 'import-tfi' (patches .tfi de jogos e packs). "
                                "Opcional: dá candidatos extras a partir de patches prontos."))
        self.ed_sf2.setPlaceholderText(tr("vazio = banco General MIDI padrão (para MIDIs sem SoundFont)"))
        self.ed_sf2.setToolTip(tr("O SoundFont do jogo (aba Extração ou GBA Mus Ripper). Para um MIDI comum, "
                               "sem SoundFont, deixe vazio: os instrumentos e a bateria vêm do banco "
                               "General MIDI padrão do programa."))
        self.ed_library.setToolTip(tr("Patches aprovados ficam aqui e servem para todas as músicas "
                                   "que usam o mesmo instrumento do SF2."))
        self.bt_load = QPushButton(tr("Abrir música"))
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
        self.lb_inst = QLabel(tr("Selecione um instrumento."))
        self.lb_inst.setWordWrap(True)
        f = self.lb_inst.font()
        f.setBold(True)
        self.lb_inst.setFont(f)
        v.addWidget(self.lb_inst)
        g = QGroupBox(tr("Gerar candidatos FM"))
        fl = QFormLayout(g)
        self.cb_budget = QComboBox()
        for text, val in ((tr("Rápido (~30 s a 2 min)"), "quick"), (tr("Normal"), "normal"), (tr("Completo (lento)"), "thorough")):
            self.cb_budget.addItem(text, val)
        self.cb_budget.setCurrentIndex(1)
        self.sp_fb = QSpinBox()
        self.sp_fb.setRange(0, 7)
        self.sp_fb.setValue(5)
        self.sp_fb.setToolTip(tr("Feedback 6–7 costuma gerar chiado no fim das notas."))
        self.ck_detune = QCheckBox(tr("Liberar detune (coro — bom para strings/ensembles)"))
        self.ck_bank = QCheckBox(tr("Incluir candidatos do banco de patches prontos"))
        self.ck_bank.setChecked(True)
        self.ck_bank_only = QCheckBox(tr("Só do banco (bem mais rápido)"))
        self.ck_lib = QCheckBox(tr("Incluir patches parecidos que já aprovei (biblioteca)"))
        self.ck_lib.setChecked(True)
        self.ck_lib.setToolTip(tr("Compara o som com os patches aprovados em outras músicas e jogos e "
                               "oferece os mais parecidos (como estão e refinados). Quanto mais você "
                               "aprova, melhores ficam as sugestões."))
        self.bt_fit = QPushButton(tr("Gerar candidatos"))
        self.bt_fit.clicked.connect(self.fit_selected)
        self.bt_auto = QPushButton(tr("⚡ Rascunho automático (todos os instrumentos)"))
        self.bt_auto.setToolTip(tr("Para cada instrumento FM ainda sem patch aprovado: gera os candidatos "
                                "e aprova o de menor custo, marcado como rascunho. Depois é só ouvir e "
                                "trocar os que não ficaram bons."))
        self.bt_auto.clicked.connect(self.auto_approve_all)
        fl.addRow(tr("Busca:"), self.cb_budget)
        fl.addRow(tr("Feedback máximo:"), self.sp_fb)
        fl.addRow(self.ck_detune)
        fl.addRow(self.ck_bank)
        fl.addRow(self.ck_bank_only)
        fl.addRow(self.ck_lib)
        fl.addRow(self.bt_fit)
        fl.addRow(self.bt_auto)
        v.addWidget(g)
        v.addWidget(QLabel(tr("Candidatos (em cada WAV: original, depois FM, nota por nota):")))
        self.ls_cand = QListWidget()
        self.ls_cand.itemDoubleClicked.connect(lambda _: self.play_candidate())
        v.addWidget(self.ls_cand, 1)
        h = QHBoxLayout()
        self.bt_play_cand = QPushButton(tr("▶ Ouvir A/B"))
        self.bt_play_cand.clicked.connect(self.play_candidate)
        self.bt_approve = QPushButton(tr("✔ Aprovar escolhido"))
        self.bt_approve.clicked.connect(self.approve_candidate)
        self.bt_reject = QPushButton(tr("✖ Recusar"))
        self.bt_reject.setToolTip(tr("Ouviu e não gostou: fica registrado no histórico de decisões "
                                  "(usado para melhorar as sugestões) e o rascunho automático o ignora."))
        self.bt_reject.clicked.connect(self.reject_candidate)
        h.addWidget(self.bt_play_cand)
        h.addWidget(self.bt_approve)
        h.addWidget(self.bt_reject)
        v.addLayout(h)
        w.addWidget(side)
        w.setStretchFactor(0, 3)
        w.setStretchFactor(1, 2)
        self._busy_widgets += [self.bt_fit, self.bt_approve, self.bt_auto, self.bt_reject]
        return w

    def _build_arrangement_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox(tr("Opções"))
        fl = QFormLayout(g)
        self.ck_pcm = QCheckBox(tr("Bateria em PCM (o canal FM 6 vira DAC)"))
        pcm_row = QWidget()
        h = QHBoxLayout(pcm_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.ck_pcm_ch = []
        for c in (1, 2, 3, 4):
            ck = QCheckBox(f"CH{c}")
            self.ck_pcm_ch.append(ck)
            h.addWidget(ck)
        h.addWidget(QLabel(tr("  (os canais não marcados ficam livres para os efeitos do jogo)")))
        psg_row = QWidget()
        hp = QHBoxLayout(psg_row)
        hp.setContentsMargins(0, 0, 0, 0)
        self.ck_psg_ch = []
        for c in (1, 2, 3):
            ck = QCheckBox(f"PSG{c}")
            ck.setToolTip(tr("Desmarque para a música não usar este tom do PSG. As notas que iriam para ele\n"
                          "ficam no FM (ou vão para outro PSG marcado); o que não couber aparece no relatório."))
            self.ck_psg_ch.append(ck)
            hp.addWidget(ck)
        hp.addWidget(QLabel(tr("  (desmarque se o PSG chiar; os desmarcados ficam mudos ou livres para o jogo)")))
        h.addStretch(1)
        self.ck_vibrato = QCheckBox(tr("Vibrato (modulação do m4a)"))
        self.cb_alloc = QComboBox()
        for label, data in ((tr("Automático (o melhor dos dois)"), "auto"),
                            (tr("Fixos por parte (como no tracker)"), "fixo"),
                            (tr("Nota a nota (dinâmico)"), "dinamico")):
            self.cb_alloc.addItem(label, data)
        self.cb_alloc.setToolTip(
            "Fixos: as partes principais ganham canal FM próprio (sem recarregar patch, o release\n"
            "termina sob a nota seguinte); vozes a mais vão para o PSG.\n"
            "Nota a nota: qualquer canal livre, a cada nota (perde menos notas em músicas densas).\n"
            "Automático: converte dos dois jeitos e fica com o que perde menos notas e recarrega\n"
            "menos patches (o relatório mostra a comparação).")
        self.ck_grid = QCheckBox(tr("Travar o andamento na grade de 60 Hz (ritmo sem oscilação; muda o andamento em até 6%)"))
        self.ck_grid.setToolTip(
            tr("Ajusta o andamento para o passo rítmico durar um número inteiro de quadros, como um\n"
            "músico faria no tracker. Ex.: 102 → 100 BPM deixa a semicolcheia em 6 quadros exatos\n"
            "em vez de alternar 6 e 5."))
        self.ck_trim = QCheckBox(tr("Começar na primeira nota (tira o silêncio do início da música)"))
        self.ck_trim.setToolTip(
            tr("Os MIDIs extraídos costumam começar com um trecho mudo (o GBA Mus Ripper põe um\n"
            "compasso inteiro). Marcado, a música começa direto na primeira nota; o loop acompanha."))
        self.ck_noise = QCheckBox(tr("Chimbais e pratos no ruído do PSG (libera o PCM para bumbo e caixa)"))
        self.ck_noise.setToolTip(
            tr("Peças metálicas do kit (detectadas pelo som: agudas e sem grave) tocam no canal de\n"
            "ruído do PSG, com o envelope da própria amostra e o volume de cada nota.\n"
            "Os canais PCM ficam para bumbo, caixa e tons, e a ROM gasta menos com amostras."))
        bal = QWidget()
        hb = QHBoxLayout(bal)
        hb.setContentsMargins(0, 0, 0, 0)
        self.ck_auto_bal = QCheckBox(tr("Automático"))
        self.sp_att = QDoubleSpinBox()
        self.sp_att.setRange(0, 30)
        self.sp_att.setSuffix(tr(" dB"))
        self.sp_att.setSingleStep(0.5)
        self.ck_auto_bal.toggled.connect(lambda on: self.sp_att.setEnabled(not on))
        hb.addWidget(self.ck_auto_bal)
        hb.addWidget(QLabel(tr("  atenuação FM/PSG:")))
        hb.addWidget(self.sp_att)
        hb.addStretch(1)
        fl.addRow(self.ck_pcm)
        fl.addRow(tr("Canais PCM da música:"), pcm_row)
        fl.addRow(tr("Canais PSG de tom:"), psg_row)
        fl.addRow(tr("Canais FM:"), self.cb_alloc)
        fl.addRow(self.ck_grid)
        fl.addRow(self.ck_trim)
        fl.addRow(self.ck_noise)
        fl.addRow(self.ck_vibrato)
        fl.addRow(tr("Equilíbrio bateria × resto:"), bal)
        self.bt_replan = QPushButton(tr("Aplicar e refazer o arranjo"))
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
        for text, val in ((tr("Automático (marcadores; senão estima)"), "auto"),
                          (tr("Só com marcador loopStart/loopEnd"), "markers"),
                          (tr("Sem loop (toca uma vez)"), "none")):
            self.cb_loop.addItem(text, val)
        self.cb_loop.currentIndexChanged.connect(self._loop_mode_changed)
        fl.addRow(tr("Modo:"), self.cb_loop)
        self.lb_loop = QLabel("—")
        self.lb_loop.setWordWrap(True)
        fl.addRow(tr("Loop atual:"), self.lb_loop)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.sp_bar = QSpinBox()
        self.sp_bar.setRange(1, 1)
        self.bt_bar = QPushButton(tr("Usar este compasso"))
        self.bt_bar.clicked.connect(self.set_loop_bar)
        self.bt_bar_auto = QPushButton(tr("Voltar à detecção"))
        self.bt_bar_auto.clicked.connect(lambda: self.set_loop_bar(auto=True))
        h.addWidget(self.sp_bar)
        h.addWidget(self.bt_bar)
        h.addWidget(self.bt_bar_auto)
        h.addStretch(1)
        fl.addRow(tr("Início do loop no compasso:"), row)
        v.addLayout(fl)
        self.bt_seam = QPushButton(tr("▶ Ouvir a emenda (fim da música → volta do loop)"))
        self.bt_seam.clicked.connect(self.play_seam)
        v.addWidget(self.bt_seam)
        tip = QLabel(tr("Sem marcador no MIDI, a volta é estimada pelo jeito que a música termina. "
                     "Ouça a emenda: se a música voltar no lugar errado, escolha o compasso certo."))
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        self._busy_widgets += [self.bt_seam, self.bt_bar, self.bt_bar_auto]
        return w

    def _build_export_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        h = QHBoxLayout()
        self.bt_preview = QPushButton(tr("Gerar prévia"))
        self.bt_preview.clicked.connect(self.make_preview)
        self.bt_play = QPushButton(tr("▶ Tocar"))
        self.bt_play.clicked.connect(lambda: self._play(self._preview_path))
        self.bt_stop = QPushButton(tr("■ Parar"))
        self.bt_stop.clicked.connect(self.stop_audio)
        for b in (self.bt_preview, self.bt_play, self.bt_stop):
            h.addWidget(b)
        h.addWidget(QLabel(tr("   Emulador do FM:")))
        self.cb_engine = QComboBox()
        self.cb_engine.addItem(tr("Nuked-OPN2 (modo YM2612, mais fiel)"), "auto")
        self.cb_engine.addItem(tr("Emulador próprio (mais rápido)"), "proprio")
        self.cb_engine.setToolTip(tr("Só muda a prévia; os arquivos XGM/VGM são os mesmos."))
        h.addWidget(self.cb_engine)
        h.addStretch(1)
        v.addLayout(h)
        g = QGroupBox(tr("Exportação"))
        fl = QFormLayout(g)
        fr = QWidget()
        hf = QHBoxLayout(fr)
        hf.setContentsMargins(0, 0, 0, 0)
        self.ck_xgm = QCheckBox(tr("XGM v1 (SGDK)"))
        self.ck_vgm = QCheckBox(tr("VGM 1.71 (players/emuladores)"))
        self.ck_fur = QCheckBox(tr("Furnace 0.6.8.3 (.fur)"))
        self.ck_fur.setToolTip(tr("Projeto do Furnace com as mesmas notas, instrumentos e bateria, para editar."))
        hf.addWidget(self.ck_xgm)
        hf.addWidget(self.ck_vgm)
        hf.addWidget(self.ck_fur)
        hf.addStretch(1)
        self.ed_name, self.ed_title, self.ed_game = QLineEdit(), QLineEdit(), QLineEdit()
        self.ed_name.setPlaceholderText(tr("padrão: nome do MIDI"))
        fl.addRow(tr("Formatos:"), fr)
        fl.addRow(tr("Nome dos arquivos:"), self.ed_name)
        fl.addRow(tr("Título (VGM):"), self.ed_title)
        fl.addRow(tr("Jogo (VGM):"), self.ed_game)
        hb = QHBoxLayout()
        self.bt_export = QPushButton(tr("Exportar"))
        self.bt_export.clicked.connect(self.export)
        self.bt_open_out = QPushButton(tr("Abrir pasta de saída"))
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
        self.bt_cancel = QPushButton(tr("Cancelar"))
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
        for c, ck in enumerate(self.ck_psg_ch, 1):
            ck.setChecked(c in s.psg_channels)
        self.ck_vibrato.setChecked(s.vibrato)
        self.ck_noise.setChecked(s.noise_metals)
        self.cb_alloc.setCurrentIndex(max(0, self.cb_alloc.findData(s.fm_alloc)))
        self.ck_grid.setChecked(s.snap_tempo)
        self.ck_trim.setChecked(s.trim_start)
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
        s.psg_channels = [c for c, ck in enumerate(self.ck_psg_ch, 1) if ck.isChecked()]
        s.vibrato = self.ck_vibrato.isChecked()
        s.noise_metals = self.ck_noise.isChecked()
        s.fm_alloc = self.cb_alloc.currentData()
        s.snap_tempo = self.ck_grid.isChecked()
        s.trim_start = self.ck_trim.isChecked()
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
            QMessageBox.information(self, APP_NAME, tr("Aguarde a tarefa atual terminar (ou cancele)."))
            return False
        self.task = Task(fn, *args, **kwargs)
        self._on_done = on_done
        self.task.signals.progress.connect(self._task_progress)
        self.task.signals.done.connect(self._task_done)
        self.task.signals.failed.connect(self._task_failed)
        self._log(f"▶ {title}")
        self._set_busy(True)
        self.pool.start(self.task)
        return True

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
            self._log(tr("■ Cancelado."))
            return
        first, _, detail = msg.partition("\n\n")
        self._error(first, detail)

    def cancel_task(self):
        if self.task:
            self.task.cancelled = True
            self._log(tr("Cancelando no próximo passo…"))

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
            p = QFileDialog.getExistingDirectory(self, tr("Escolha a pasta"), start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, tr("Escolha o arquivo"), start, filt or "")
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
        self.setWindowTitle(APP_TITLE)

    def open_project(self):
        p, _ = QFileDialog.getOpenFileName(self, tr("Abrir projeto"), str(Path.home()),
                                           tr("Projeto ({0})").format('*' + PROJECT_SUFFIX))
        if not p:
            return
        try:
            s = ProjectSettings.load(p)
        except (OSError, ValueError, TypeError) as e:
            self._error(tr("Não consegui ler o projeto: {0}").format(e))
            return
        self.project_path = Path(p)
        self._settings_to_widgets(s)
        self._load(s)

    def save_project(self):
        if self.project_path is None:
            return self.save_project_as()
        s = self.project.settings if self.project else ProjectSettings()
        self._widgets_to_settings(s)
        if self.project and self.project.song is not None:
            try:
                new = self.project.pin_patches()
            except Exception as e:          # travar é extra: não impede salvar
                new = []
                self._log(tr("⚠ não consegui travar os patches: {0}").format(e))
            if new:
                self._log(tr("🔒 {0} patch(es) travado(s) no projeto: ").format(len(new)) + ", ".join(new))
                self._fill_instruments(self.project.instruments())
        self.project_path = s.save(self.project_path)
        n = len(s.pinned_patches or {})
        self._log(tr("Projeto salvo: {0} ({1} patch(es) FM travado(s))").format(self.project_path, n))

    def save_project_as(self):
        start = str(Path(self.ed_out.text() or Path.home()) / (Path(self.ed_midi.text()).stem + PROJECT_SUFFIX))
        p, _ = QFileDialog.getSaveFileName(self, tr("Salvar projeto"), start, tr("Projeto ({0})").format('*' + PROJECT_SUFFIX))
        if p:
            self.project_path = Path(p)
            self.save_project()

    def open_from_extraction(self, midi: str, sf2: str, library: str | None):
        """Botão "Abrir na conversão" da aba Extração."""
        if self.task is not None:
            self._error(tr("Espere a tarefa em andamento terminar."))
            return
        self.ed_midi.setText(midi)
        self.ed_sf2.setText(sf2)
        if library:
            self.ed_library.setText(library)
        self.main_tabs.setCurrentIndex(1)
        self.load_song()

    def load_song(self):
        s = self._widgets_to_settings()
        if self.project and self.project.settings.midi == s.midi:
            s.loop_start_tick = self.project.settings.loop_start_tick
            s.loop_end_tick = self.project.settings.loop_end_tick
        self._load(s)

    def _load(self, s: ProjectSettings):
        errs = [e for e in s.validate() if "MIDI" in e or "SF2" in e]
        if errs:
            self._error(tr("Antes de abrir: ") + "; ".join(errs) + ".")
            return
        self.qsettings.setValue("library", s.library)
        self.qsettings.setValue("bank", s.bank)
        self.qsettings.setValue("out_dir", s.out_dir)
        self.stop_audio()
        project = Project(s)

        def work(progress):
            warnings = project.load(progress)
            return warnings, project.instruments(), project.loop()
        self._run(tr("Abrindo {0}").format(Path(s.midi).name), work, lambda r: self._loaded(project, *r))

    def _loaded(self, project: Project, warnings, rows, loop):
        self.project = project
        self.setWindowTitle(f"{APP_TITLE} — {Path(project.settings.midi).name}")
        for w in warnings:
            self._log(f"⚠ {w}")
        self._fill_instruments(rows)
        self.tx_plan.setPlainText(project.plan_text())
        self.sp_bar.setRange(1, project.bar_count())
        self._show_loop(loop)
        self._set_loaded(True)
        self._set_busy(False)
        if project.trim_seconds > 0:
            self._log(tr("Silêncio do início removido: {0:.2f} s (a música começa na primeira nota).").format(project.trim_seconds))
        n_prov = sum(1 for r in rows if r.status == "provisório")
        n_pin = sum(1 for r in rows if r.pinned)
        self._log(tr("✔ {0} instrumentos; {1} ainda sem patch FM aprovado").format(len(rows), n_prov)
                  + (tr("; {0} com patch travado no projeto.").format(n_pin) if n_pin else "."))
        if n_pin:
            self._offer_pin_update(on_open=True)
        if project.sf_notice:
            QMessageBox.information(self, "SoundFont", project.sf_notice)

    # ------------------------------------------------------------- patches travados
    def _offer_pin_update(self, on_open: bool = False) -> bool:
        try:
            diffs = self.project.pin_differences()
        except Exception as e:
            self._log(tr("⚠ não consegui comparar com a biblioteca: {0}").format(e))
            return False
        if not diffs:
            if not on_open:
                QMessageBox.information(self, APP_NAME, tr("Os patches travados já são iguais aos da biblioteca."))
            return False
        lista = "\n".join(tr("• {0}  (travado: {1} · biblioteca: {2})").format(d['label'], d['pinned_at'] or '?', d['library_at'] or '?') for d in diffs)
        msg = (tr("A biblioteca tem patches diferentes dos travados neste projeto para {0} "
                  "instrumento(s):\n\n{1}\n\nAtualizar esta música para os patches da biblioteca?\n(Não = manter "
                  "os travados: a música continua soando como foi salva.)").format(len(diffs), lista))
        if QMessageBox.question(self, APP_NAME, msg) != QMessageBox.Yes:
            self._log(tr("🔒 Mantidos os patches travados do projeto."))
            return False
        done = self.project.update_pins([d["key"] for d in diffs])
        self._fill_instruments(self.project.instruments())
        self._log(tr("🔒 Travas atualizadas pela biblioteca: ") + ", ".join(done) + tr(". Salve o projeto para manter."))
        return True

    def update_pinned(self):
        if not self.project or self.project.song is None:
            self._error(tr("Abra uma música primeiro."))
            return
        if not self.project.settings.pinned_patches:
            QMessageBox.information(self, APP_NAME, tr("Esta música ainda não tem patches travados "
                                    "(eles são travados ao salvar o projeto)."))
            return
        self._offer_pin_update()

    def unpin_all(self):
        if not self.project or not self.project.settings.pinned_patches:
            QMessageBox.information(self, APP_NAME, tr("Esta música não tem patches travados."))
            return
        if QMessageBox.question(self, APP_NAME, tr("Destravar todos os patches desta música? Ela passa a usar "
                                "os patches atuais da biblioteca (até salvar o projeto de novo, que trava "
                                "outra vez).")) != QMessageBox.Yes:
            return
        self.project.unpin()
        self._fill_instruments(self.project.instruments())
        self._log(tr("🔓 Patches destravados: a música segue a biblioteca até o próximo salvamento."))

    # ------------------------------------------------------------- instrumentos
    def _fill_instruments(self, rows):
        sel = self._selected_row()
        key = sel.key if sel else None
        self._rows = rows
        self.table.blockSignals(True)            # sem reações no meio do preenchimento
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r.label, ", ".join(f"C{c}" for c in r.channels), str(r.notes),
                    ", ".join(tr(x) for x in r.roles),
                    " + ".join(r.pools), r.chip_text() or "—",
                    tr(r.status) + (tr(" (rascunho)") if r.auto else "") + (" 🔒" if r.pinned else "")
                    + (tr(" ({0} cand.)").format(len(r.candidates)) if r.candidates and r.status != "aprovado" else "")]
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
            return (tr("Aparece depois de converter (aba Arranjo → \"Aplicar e refazer o arranjo\" ou prévia).\n"
                    "Informativo: o driver escolhe o canal nota a nota."))
        lines = [tr("{0}: {1} nota(s)").format(k, v) for k, v in sorted(r.chip_use.items(), key=lambda kv: -kv[1])]
        return (tr("Canais do chip usados na última conversão (o driver escolhe nota a nota):\n")
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
            self.lb_inst.setText(tr("Selecione um instrumento."))
            return
        info = tr("{0} — canais {1} — {2}").format(r.label, ', '.join(map(str, r.channels)), tr(r.status))
        if r.chip_use:
            info += tr("\nNo chip: {0}").format(r.chip_text())
        if not r.needs_fm:
            info += (tr("\nEste instrumento não toca no FM (") + " + ".join(r.pools) +
                     tr("), não precisa de patch."))
        self.lb_inst.setText(info)
        self.bt_fit.setEnabled(r.needs_fm and self.task is None)
        current = 0
        for i, c in enumerate(r.candidates):
            it = QListWidgetItem((tr("✔ APROVADO  ") if c.approved else tr("✖ RECUSADO  ") if c.rejected else "")
                                 + f"#{c.rank}  {c.summary}")
            if c.rejected and not c.approved:
                it.setForeground(Qt.gray)
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
        use_lib = self.ck_lib.isChecked()
        self._sync_settings()
        project = self.project

        def work(progress):
            project.fit(r.key, budget=budget, max_fb=fb, detune=det, progress=progress,
                        use_bank=use_bank, bank_only=bank_only and use_bank, use_library=use_lib)
            return project.instruments()
        self._run(tr("Gerando candidatos FM para {0}").format(r.label), work, self._after_instruments)

    def auto_approve_all(self):
        if not self.project or self.project.song is None:
            self._error(tr("Abra uma música primeiro."))
            return
        rows = [r for r in self._rows if r.needs_fm and r.status == "provisório"]
        if not rows:
            QMessageBox.information(self, APP_NAME, tr("Todos os instrumentos FM já têm patch aprovado."))
            return
        budget, fb = self.cb_budget.currentData(), self.sp_fb.value()
        use_bank, use_lib = self.ck_bank.isChecked(), self.ck_lib.isChecked()
        if QMessageBox.question(
                self, APP_NAME,
                tr("Gerar candidatos e aprovar automaticamente o melhor para {0} instrumento(s)?\n\n").format(len(rows))
                + "\n".join(f"• {r.label}" for r in rows[:15]) + ("\n…" if len(rows) > 15 else "")
                + tr("\n\nBusca: {0}. Pode levar alguns minutos por instrumento.\nOs patches entram como RASCUNHO: "
                     "ouça a música e troque os que não ficarem bons.").format(self.cb_budget.currentText())) \
                != QMessageBox.Yes:
            return
        self.stop_audio()
        self._sync_settings()
        project = self.project

        def work(progress):
            done = project.auto_approve(budget=budget, max_fb=fb, use_bank=use_bank,
                                        use_library=use_lib, progress=progress)
            return done, project.instruments()

        def finished(result):
            done, rows = result
            self._fill_instruments(rows)
            for d in done:
                self._log(tr("⚡ rascunho: {0}").format(d))
            self._log(tr("✔ {0} instrumento(s) com patch de rascunho. Ouça a prévia da música e aprove outro "
                         "candidato onde precisar.").format(len(done)))
        self._run(tr("Rascunho automático"), work, finished)

    def _after_instruments(self, rows):
        self._fill_instruments(rows)
        self._log(tr("✔ Pronto. Ouça os candidatos e aprove o que soar melhor."))

    def _after_approve(self, result):
        info, rows = result
        self._fill_instruments(rows)
        self._log(tr("✔ Aprovado: {0} candidato #{1} → {2}").format(info['label'], info['rank'], self.project.library_dir / (info['library_id'] + '.json')))
        r = next((x for x in rows if x.label == info["row_label"]), None)
        if r is not None and r.status != "aprovado":
            self._error(tr("O patch foi gravado, mas o instrumento continua sem patch aprovado na tabela. "
                        "Confira se a pasta da biblioteca é a mesma usada na conversão e me mande o "
                        "texto do registro abaixo."))

    def _selected_candidate(self):
        it = self.ls_cand.currentItem()
        return it.data(Qt.UserRole) if it else None

    def play_candidate(self):
        c, r = self._selected_candidate(), self._selected_row()
        if c:
            self._play(c.wav)
            if self.project and r:
                self.project.listened(r.key, c.rank)

    def reject_candidate(self):
        r, c = self._selected_row(), self._selected_candidate()
        if not (self.project and r and c):
            self._error(tr("Selecione o instrumento e o candidato."))
            return
        if c.approved:
            self._error(tr("Este candidato está aprovado. Aprove outro antes de recusar este."))
            return
        try:
            self.project.reject(r.key, c.rank)
        except ValueError as e:
            self._error(str(e))
            return
        self._log(tr("✖ {0}: candidato #{1} recusado ({2}).").format(r.label, c.rank, c.origin or 'busca'))
        self._fill_instruments(self.project.instruments())

    def show_decisions(self):
        from mdconv.library.decisions import summary
        s = summary()
        ev = s["eventos"]
        lines = [tr("Arquivo: {0}").format(s['arquivo']), "",
                 tr("Aprovados: {0} · Recusados: {1} · Rascunhos automáticos: {2} · Audições: {3}").format(ev.get('aprovado', 0), ev.get('recusado', 0), ev.get('rascunho_auto', 0), ev.get('ouviu', 0)), ""]
        if s["por_origem"]:
            lines.append(tr("Por origem do candidato (aprovados / recusados):"))
            for k, v in sorted(s["por_origem"].items()):
                lines.append(f"  • {k}: {v.get('aprovado', 0)} / {v.get('recusado', 0)}")
        lines += ["", tr("Cada aprovação e recusa fica registrada com o tipo de instrumento, as opções "
                  "da busca e os custos medidos. Com algumas centenas de decisões, esses dados "
                  "servem para ajustar as sugestões ao seu ouvido.")]
        QMessageBox.information(self, APP_NAME, "\n".join(lines))

    def approve_candidate(self):
        r, c = self._selected_row(), self._selected_candidate()
        if not self.project:
            return
        if r is None:
            self._error(tr("Selecione o instrumento na tabela."))
            return
        if c is None:
            self._error(tr("Selecione um candidato na lista (gere os candidatos antes, se a lista estiver vazia)."))
            return
        project = self.project

        def work(progress):
            progress(tr("Aprovando #{0} de {1}…").format(c.rank, r.label))
            info = project.approve(r.key, c.rank)
            info["row_label"] = r.label
            return info, project.instruments()
        self._run(tr("Aprovando"), work, self._after_approve)

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
            progress(tr("Refazendo o arranjo…"))
            project.replan()
            c = project.convert(progress)
            return project.instruments(), project.report(), c
        self._run(tr("Arranjo"), work, self._after_replan)

    def _after_replan(self, result):
        rows, report, c = result
        self._fill_instruments(rows)
        if self.project:
            self.sp_bar.setRange(1, self.project.bar_count())
            if self.project.trim_seconds > 0:
                self._log(tr("Silêncio do início removido: {0:.2f} s.").format(self.project.trim_seconds))
        self.tx_plan.setPlainText(report)
        if c.balance:
            self._log(tr("Equilíbrio automático: FM/PSG −{0} dB.").format(c.balance['fm_psg_att_db']))
        self._show_loop(c.loop)

    # ------------------------------------------------------------- loop
    def _show_loop(self, loop):
        if loop is None:
            self.lb_loop.setText(tr("sem loop — a música toca uma vez e para."))
            return
        bar = self.project.tick_to_bar(loop.start_tick) if self.project else 0
        txt = (tr("de {0:.2f} s (compasso {1:.0f}) até {2:.2f} s — origem: {3}. {4}").format(loop.start_s, bar, loop.end_s, loop.source, loop.detail))
        if loop.estimated:
            txt = tr("⚠ ESTIMADO — ") + txt
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
        self._run(tr("Emenda do loop"), self.project.render_seam, self._play)

    # ------------------------------------------------------------- prévia / exportação
    def make_preview(self):
        if not self.project:
            return
        self._sync_settings()
        self.stop_audio()

        def done(path):
            self._preview_path = path
            self._log(tr("✔ Prévia: {0}").format(path))
            self._play(path)
        self._run(tr("Prévia da música"), self.project.render_preview, done)

    def export(self):
        if not self.project:
            return
        self._sync_settings()
        errs = self.project.settings.validate()
        if not self.project.settings.formats:
            errs.append(tr("marque ao menos um formato (XGM, VGM ou Furnace)"))
        if errs:
            self._error("; ".join(errs))
            return
        self.stop_audio()

        def done(lines):
            self.tx_export.setPlainText("\n".join(lines))
            self._log(tr("✔ Exportado em ") + str(self.project.out_dir))
        self._run(tr("Exportação"), self.project.export, done)

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
            self._error(tr("Arquivo não encontrado: {0}").format(path))
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

    # ------------------------------------------------------------- ferramentas (subtela)
    def open_tools(self, tab: int | None = None):
        """Subtela com os conversores à parte (VGM direto, efeitos de chip, WAV, banco .tfi)."""
        from mdconv.gui.tools_dialog import ToolsDialog
        if getattr(self, "_tools", None) is None:
            self._tools = ToolsDialog(self)
        if tab is not None:
            self._tools.tabs.setCurrentIndex(tab)
        self._tools.show()
        self._tools.raise_()
        self._tools.activateWindow()

    def build_tfi_bank_job(self, src: str, dst: str, on_done=None):
        from mdconv.library.bank import build_bank
        workers = max(1, (os.cpu_count() or 2) - 1)

        def work(progress):
            return build_bank(src, dst, workers=workers, progress=progress)

        def done(s):
            self.ed_bank.setText(dst)
            o = s["operator_order_check"]
            msg = (tr("Banco pronto: {0} patches diferentes de {1} arquivos ({2} com defeito; {3} usam SSG-EG, "
                      "importados sem ele). Ordem dos operadores conferida em {4} patches de algoritmo 4.").format(s['in_bank'], s['files'], s['n_errors'], s['ssg_eg_ignored'], o['alg4']))
            self._log("✔ " + msg)
            if on_done:
                on_done()
            QMessageBox.information(self, APP_NAME, msg + tr("\n\nO campo 'Banco de patches prontos' já aponta "
                                    "para ele."))
        return self._run(tr("Montando o banco de patches .tfi (milhares de patches levam alguns minutos)"),
                         work, done)

    def convert_vgm_job(self, files, dst, grid=False, trim=True, routing=None, on_done=None,
                        loop_start=None, loop_end=None, pcm_channel=1, opl_routing=None, style=None,
                        neo_options=None, subfolders=False, pcm_atten_db=0.0):
        """VGM de AY-3-8910 (Moon Patrol, MSX…), YM2203 (Ghosts 'n Goblins…) ou do próprio Mega
        Drive: conversão direta para XGM/VGM/.fur. ``routing``: rotas manuais dos canais do AY;
        ``loop_start``/``loop_end``: loop escolhido à mão (segundos)."""
        import json

        from mdconv.vgmconv import convert_file
        neo = neo_options
        if neo is None:
            try:                                # as mesmas opções do Neo Geo da aba Extração
                neo = json.loads(self.qsettings.value("extract/neo_options", "{}") or "{}")
            except (ValueError, TypeError):
                neo = {}

        def work(progress):
            out, bad = [], 0
            for f in files:
                progress(f"▶ {Path(f).name}")
                try:
                    out += convert_file(f, dst, grid=grid, trim=trim, routing=routing, log=progress,
                                        loop_start=loop_start, loop_end=loop_end, pcm_channel=pcm_channel,
                                        opl_routing=opl_routing, style=style, neo_options=neo,
                                        subfolder=subfolders, pcm_atten_db=pcm_atten_db)
                except Exception as exc:              # um arquivo ruim não para o lote
                    bad += 1
                    progress(f"✖ {Path(f).name}: {exc}")
            return out, bad

        def done(result):
            paths, bad = result
            self._log(tr("✔ {0} música(s) convertida(s) em {1}").format(len(files) - bad, dst)
                      + (tr("; {0} com erro (ver registro)").format(bad) if bad else ""))
            if on_done:
                on_done()
            QMessageBox.information(self, APP_NAME, tr("Pronto: {0} arquivo(s) em\n{1}").format(len(paths), dst)
                                    + (tr("\n\n{0} arquivo(s) com erro: veja o registro.").format(bad) if bad else ""))

        self.main_tabs.setCurrentIndex(1)
        return self._run(tr("Convertendo {0} VGM").format(len(files)), work, done)

    def _chip_sfx_job(self, title, items, dst, game="", on_done=None):
        """items: lista de (nome, função que devolve o AyVgm[, (canais PSG, canais FM)]).
        Canais 0–2 = PSG tons 1–3; 0–5 = FM 1–6."""
        from mdconv import chipsfx

        def work(progress):
            out = []
            for item in items:
                name, get = item[0], item[1]
                psg, fm = item[2] if len(item) > 2 else (chipsfx.DEFAULT_PSG, chipsfx.DEFAULT_FM)
                try:
                    sfx = chipsfx.from_ay(get(), name, psg_free=psg, fm_free=fm)
                    out += chipsfx.write_outputs(sfx, dst, game=game)
                    progress(tr("✔ {0}: {1} quadros, {2} escritas").format(name, len(sfx.frames), sfx.n_writes))
                    for line in sfx.report:
                        progress("   " + line)
                except Exception as exc:              # um efeito ruim não para o lote
                    progress(f"✖ {name}: {exc}")
            return out

        def done(paths):
            self._log(tr("✔ Efeitos prontos em {0}: .vgm, .c (SGDK) e prévia de cada um").format(dst))
            if on_done:
                on_done()
            QMessageBox.information(self, APP_NAME, tr("Pronto: {0} arquivo(s) em\n{1}\n\nNo jogo: chame nome_play() para disparar e nome_update() "
                                                       "uma vez por quadro.").format(len(paths), dst))

        self.main_tabs.setCurrentIndex(1)
        return self._run(title, work, done)

    def chip_sfx_files_job(self, files, dst, psg_free, fm_free, on_done=None):
        from mdconv.ay.convert import read_vgm
        items = [(Path(f).stem, (lambda f=f: read_vgm(f)), (tuple(psg_free), tuple(fm_free))) for f in files]
        return self._chip_sfx_job(tr("Convertendo {0} efeito(s) de chip").format(len(files)), items, dst, on_done=on_done)

    def wav_sfx_job(self, files, dst, rate=14000, compress=True, same_gain=False, on_done=None):
        """WAV de efeitos/vozes → PCM de 8 bits na taxa do driver, sem o som "rouco"."""
        from mdconv.sfx import convert_batch

        def work(progress):
            return convert_batch(files, dst, rate=rate, compress=compress, same_gain=same_gain, log=progress)

        def done(results):
            kb = sum(r.rom_bytes for r in results) / 1024
            self._log(tr("✔ {0} efeito(s) prontos em {1} ({2:.0f} KB na ROM). No .res: WAV nome \"arquivo.wav\" XGM").format(len(results), dst, kb))
            if on_done:
                on_done()
            QMessageBox.information(self, APP_NAME, tr("Pronto: {0} arquivo(s) em\n{1}\n\n{2:.0f} KB na ROM. Prévias para ouvir no PC na subpasta "
                                                       "'previa'.").format(len(results), dst, kb))

        self.main_tabs.setCurrentIndex(1)
        return self._run(tr("Preparando {0} efeito(s) sonoro(s)").format(len(files)), work, done)

    # ------------------------------------------------------------- outros
    def about(self):
        QMessageBox.about(self, APP_NAME,
                          tr("<b>{0}</b> — versão {1}<br><br>Converte músicas (MIDI + SF2) para o som do Mega Drive: "
                             "YM2612 (FM), SN76489 (PSG) e PCM, exportando XGM v1 (SGDK), VGM e "
                             "Furnace.<br><br><b>Origens suportadas:</b><br>• <b>GBA</b> — MIDI + SF2 da aba Extração "
                             "(motor de som da Nintendo) ou do GBA Mus Ripper<br>• Arcades Capcom <b>CPS1</b> — MIDI + "
                             "SF2 da aba Extração, com os timbres FM originais prontos como biblioteca de patches.<br>• "
                             "<b>MIDI comum</b> (General MIDI), com ou sem SoundFont — sem SF2, usa o banco General "
                             "MIDI padrão do programa.<br>• VGM de <b>AY-3-8910</b> (Moon Patrol, MSX, Spectrum…) e "
                             "<b>YM2203</b> (Ghosts 'n Goblins…) — conversão direta chip a chip (menu "
                             "Ferramentas).<br><br>Aba <b>Extração</b>: extrator de áudio {2} (parte CPS1 portada do "
                             "VGMTrans, licença zlib).<br><br>Software livre.<br><br>Prévia do FM com o "
                             "<b>Nuked-OPN2</b> © Alexey Khokholov (Nuke.YKT), licença GNU LGPL 2.1 ou posterior — "
                             "código-fonte e licença na pasta <code>mdconv/chips/native/nuked_opn2</code>.").format(APP_NAME, __version__, extract_version))

    def closeEvent(self, ev):  # noqa: N802 (nome do Qt)
        if self.task is not None or self.extract_tab.busy():
            if QMessageBox.question(self, APP_NAME, tr("Há uma tarefa em andamento. Sair mesmo assim?")) \
                    != QMessageBox.Yes:
                ev.ignore()
                return
            if self.task is not None:
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
