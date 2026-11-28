"""Aba Catálogo: lista dos jogos de Mega Drive com a compatibilidade que o usuário marcou (bolinha
cinza / amarela / verde / vermelha), vínculo com a ROM aberta na Extração e exportação em CSV."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from mdconv import catalog as cat
from mdconv.i18n import tr

COLORS = {cat.CINZA: "#9e9e9e", cat.AMARELO: "#f2b705", cat.VERDE: "#2e9e44", cat.VERMELHO: "#d33c32"}
NEXT = {cat.CINZA: cat.VERDE, cat.AMARELO: cat.VERDE, cat.VERDE: cat.VERMELHO, cat.VERMELHO: cat.CINZA}
COL_ST, COL_NOME, COL_FR, COL_REG, COL_TIPO, COL_PERF, COL_OBS = range(7)


def status_label(st: str) -> str:
    return {cat.CINZA: tr("Não testado"), cat.AMARELO: tr("Analisado, falta ouvir"),
            cat.VERDE: tr("Funciona"), cat.VERMELHO: tr("Não funciona")}[st]


def _dot(color: str) -> QIcon:
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(QColor(color).darker(130))
    p.drawEllipse(2, 2, 12, 12)
    p.end()
    return QIcon(pm)


class CatalogTab(QWidget):
    """``current_rom()`` devolve o caminho da ROM aberta na Extração (ou None)."""

    changed = Signal()

    def __init__(self, current_rom=None, parent=None):
        super().__init__(parent)
        self.current_rom = current_rom or (lambda: None)
        self.cat = cat.Catalog()
        self.icons = {s: _dot(c) for s, c in COLORS.items()}
        self.letter = None
        self.rom = None            # dados da ROM aberta (rom_info) + "path"
        self.pending = None        # (evento, mensagem) da ROM aberta ainda não vinculada
        self.suggest = None        # id sugerido para a ROM aberta

        v = QVBoxLayout(self)
        intro = QLabel(tr("Marque aqui se cada jogo funcionou no conversor: clique na bolinha (cinza = não "
                          "testado, verde = funciona, vermelho = não funciona; amarelo = analisado, falta ouvir). "
                          "Depois exporte a lista em CSV e mande para quem mantém o conversor."))
        intro.setWordWrap(True)
        v.addWidget(intro)

        # ROM aberta na Extração
        self.bar = QFrame()
        self.bar.setFrameShape(QFrame.StyledPanel)
        hb = QHBoxLayout(self.bar)
        self.lb_rom = QLabel()
        self.lb_rom.setWordWrap(True)
        hb.addWidget(self.lb_rom, 1)
        self.bt_confirm = QPushButton(tr("Confirmar"))
        self.bt_confirm.clicked.connect(self.confirm_suggestion)
        hb.addWidget(self.bt_confirm)
        self.bt_link = QPushButton(tr("Vincular ao jogo selecionado"))
        self.bt_link.setToolTip(tr("A ROM aberta na aba Extração é o jogo selecionado na lista."))
        self.bt_link.clicked.connect(self.link_selected)
        hb.addWidget(self.bt_link)
        self.bt_add_rom = QPushButton(tr("Não está na lista…"))
        self.bt_add_rom.setToolTip(tr("Adiciona o jogo da ROM aberta à lista e vincula."))
        self.bt_add_rom.clicked.connect(self.add_for_rom)
        hb.addWidget(self.bt_add_rom)
        v.addWidget(self.bar)

        # letras
        hl = QHBoxLayout()
        hl.setSpacing(2)
        self.letter_buttons = []
        for t in [tr("Todos"), "#"] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]:
            b = QPushButton(t)
            b.setCheckable(True)
            b.setFixedWidth(46 if len(t) > 1 else 24)
            b.clicked.connect(lambda _=False, t=t: self.set_letter(None if len(t) > 1 else t))
            hl.addWidget(b)
            self.letter_buttons.append(b)
        self.letter_buttons[0].setChecked(True)
        hl.addStretch(1)
        v.addLayout(hl)

        # filtros
        hf = QHBoxLayout()
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText(tr("Buscar nome, franquia ou observação…"))
        self.ed_find.textChanged.connect(self.apply_filter)
        hf.addWidget(self.ed_find, 2)
        hf.addWidget(QLabel(tr("Tipo:")))
        self.cb_kind = QComboBox()
        hf.addWidget(self.cb_kind)
        hf.addWidget(QLabel(tr("Franquia:")))
        self.cb_fr = QComboBox()
        self.cb_fr.setMinimumWidth(160)
        hf.addWidget(self.cb_fr)
        hf.addWidget(QLabel(tr("Situação:")))
        self.cb_st = QComboBox()
        self.cb_st.addItem(tr("Todas"), None)
        for s in (cat.CINZA, cat.AMARELO, cat.VERDE, cat.VERMELHO):
            self.cb_st.addItem(self.icons[s], status_label(s), s)
        hf.addWidget(self.cb_st)
        for c in (self.cb_kind, self.cb_fr, self.cb_st):
            c.currentIndexChanged.connect(self.apply_filter)
        v.addLayout(hf)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["", tr("Nome do jogo"), tr("Franquia"), tr("Região"), tr("Tipo"),
                                              tr("Perfil"), tr("Observação")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.cellClicked.connect(self._clicked)
        self.table.cellDoubleClicked.connect(self._double)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_ST, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_ST, 28)
        for c, wdt in ((COL_NOME, 300), (COL_FR, 140), (COL_REG, 60), (COL_TIPO, 90), (COL_PERF, 50)):
            self.table.setColumnWidth(c, wdt)
        h.setStretchLastSection(True)
        v.addWidget(self.table, 1)

        hb2 = QHBoxLayout()
        self.lb_stats = QLabel()
        hb2.addWidget(self.lb_stats, 1)
        b = QPushButton(tr("Adicionar jogo…"))
        b.clicked.connect(self.add_game)
        hb2.addWidget(b)
        self.ck_all = QCheckBox(tr("Exportar também os não testados"))
        hb2.addWidget(self.ck_all)
        b = QPushButton(tr("Exportar CSV…"))
        b.clicked.connect(self.export_csv)
        hb2.addWidget(b)
        v.addLayout(hb2)

        self.populate()
        self.show_rom()

    # ------------------------------------------------------------- lista
    def populate(self):
        self.cat.refresh_profiles()
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.cat.games))
        self.row_of = {}
        for r, g in enumerate(sorted(self.cat.games, key=lambda g: g["nome"].lower())):
            self.row_of[g["id"]] = r
            it = QTableWidgetItem()
            it.setData(Qt.UserRole, g["id"])
            self.table.setItem(r, COL_ST, it)
            for c, val in ((COL_NOME, g["nome"]), (COL_FR, g.get("franquia", "")),
                           (COL_REG, g.get("regiao", "")), (COL_TIPO, tr(g.get("tipo", "")))):
                self.table.setItem(r, c, QTableWidgetItem(val))
            self.table.setItem(r, COL_PERF, QTableWidgetItem("✓" if self.cat.has_profile(g) else ""))
            self.table.item(r, COL_PERF).setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, COL_OBS, QTableWidgetItem())
            self._paint(g["id"])
        self.table.setUpdatesEnabled(True)
        cur_k, cur_f = self.cb_kind.currentData(), self.cb_fr.currentData()
        for cb, items, cur in ((self.cb_kind, self.cat.kinds(), cur_k), (self.cb_fr, self.cat.franchises(), cur_f)):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem(tr("Todos"), None)
            for x in items:
                cb.addItem(tr(x) if cb is self.cb_kind else x, x)
            i = cb.findData(cur)
            cb.setCurrentIndex(max(0, i))
            cb.blockSignals(False)
        self.apply_filter()

    def _paint(self, gid):
        r = self.row_of.get(gid)
        if r is None:
            return
        e = self.cat.info(gid)
        st = e.get("status", cat.CINZA)
        it = self.table.item(r, COL_ST)
        it.setIcon(self.icons[st])
        it.setToolTip(status_label(st))
        obs = e.get("obs", "")
        if e.get("erro"):
            obs = (obs + " — " if obs else "") + tr("análise: ") + e["erro"]
        if e.get("roms"):
            rr = e["roms"][-1]
            obs = (obs + " " if obs else "") + f"[{rr.get('serial', '')} {rr.get('checksum', '')}]"
        self.table.item(r, COL_OBS).setText(obs)
        self.table.item(r, COL_OBS).setToolTip(obs)

    def set_letter(self, letter):
        self.letter = letter
        for b in self.letter_buttons:
            b.setChecked(b.text() == (letter or tr("Todos")))
        self.apply_filter()

    def apply_filter(self):
        find = self.ed_find.text().strip().lower()
        kind, fr, st = self.cb_kind.currentData(), self.cb_fr.currentData(), self.cb_st.currentData()
        for g in self.cat.games:
            r = self.row_of[g["id"]]
            n = g["nome"]
            ok = True
            if self.letter == "#":
                ok = not n[:1].isalpha()
            elif self.letter:
                ok = n[:1].upper() == self.letter
            if ok and kind:
                ok = g.get("tipo") == kind
            if ok and fr:
                ok = g.get("franquia") == fr
            if ok and st:
                ok = self.cat.status(g["id"]) == st
            if ok and find:
                ok = (find in n.lower() or find in g.get("franquia", "").lower()
                      or find in self.cat.info(g["id"]).get("obs", "").lower())
            self.table.setRowHidden(r, not ok)
        self.update_stats()

    def update_stats(self):
        c = self.cat.counts()
        self.lb_stats.setText(tr("{0} jogos · {1} funcionam · {2} não funcionam · {3} falta ouvir · {4} não testados")
                              .format(len(self.cat.games), c[cat.VERDE], c[cat.VERMELHO], c[cat.AMARELO],
                                      c[cat.CINZA]))

    def _gid(self, row):
        it = self.table.item(row, COL_ST)
        return it.data(Qt.UserRole) if it else None

    def selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self._gid(rows[0].row()) if rows else None

    def _clicked(self, row, col):
        if col == COL_ST:
            gid = self._gid(row)
            self.cat.mark(gid, NEXT[self.cat.status(gid)])
            self._paint(gid)
            self.update_stats()
            self.changed.emit()

    def _double(self, row, col):
        if col != COL_ST:
            self.edit_obs(self._gid(row))

    def edit_obs(self, gid):
        g = self.cat.by_id[gid]
        t, ok = QInputDialog.getText(self, tr("Observação"), g["nome"], text=self.cat.info(gid).get("obs", ""))
        if ok:
            self.cat.set_obs(gid, t)
            self._paint(gid)

    def context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        gid = self._gid(row)
        m = QMenu(self)
        acts = {}
        for s in (cat.VERDE, cat.VERMELHO, cat.AMARELO, cat.CINZA):
            acts[m.addAction(self.icons[s], status_label(s))] = s
        m.addSeparator()
        a_obs = m.addAction(tr("Escrever observação…"))
        a_link = m.addAction(tr("Vincular a ROM aberta a este jogo"))
        a_link.setEnabled(self.rom is not None)
        a_unlink = m.addAction(tr("Desfazer vínculos com ROMs"))
        a_unlink.setEnabled(bool(self.cat.info(gid).get("roms")))
        a_rm = None
        if self.cat.by_id[gid].get("extra"):
            a_rm = m.addAction(tr("Remover da lista (adicionado por você)"))
        a = m.exec(self.table.viewport().mapToGlobal(pos))
        if a is None:
            return
        if a in acts:
            self.cat.mark(gid, acts[a])
        elif a == a_obs:
            self.edit_obs(gid)
        elif a == a_link:
            self.link(gid)
        elif a == a_unlink:
            self.cat.unlink(gid)
            self.show_rom()
        elif a_rm is not None and a == a_rm:
            self.cat.remove_game(gid)
            self.populate()
            return
        self._paint(gid)
        self.update_stats()
        self.changed.emit()

    def goto(self, gid):
        r = self.row_of.get(gid)
        if r is None:
            return
        if self.table.isRowHidden(r):
            self.ed_find.clear()
            self.set_letter(None)
            for cb in (self.cb_kind, self.cb_fr, self.cb_st):
                cb.setCurrentIndex(0)
        self.table.selectRow(r)
        self.table.scrollToItem(self.table.item(r, COL_NOME), QAbstractItemView.PositionAtCenter)

    # ------------------------------------------------------------- ROM aberta na Extração
    def rom_event(self, ev: dict):
        """Chamado pela aba Extração: ``{"path", "event": "aberta"|"perfil"|"sem_perfil"|"analise_ok"|
        "analise_falhou", "message"}``."""
        path = ev.get("path")
        if not path:
            return
        if self.rom is None or self.rom.get("path") != path:
            try:
                self.rom = dict(cat.rom_info(path), path=path)
            except Exception:  # noqa: BLE001 — não é ROM de Mega Drive
                self.rom = None
                self.show_rom()
                return
            self.pending = None
        gid, how = self.cat.find(self.rom)
        kind = ev.get("event")
        if kind in ("perfil", "analise_ok", "analise_falhou"):
            if gid and how in ("vinculo", "arquivo"):
                if how == "arquivo":
                    self.cat.link(gid, self.rom)
                self.cat.record(gid, kind, ev.get("message", ""), self.rom)
                self._paint(gid)
                self.update_stats()
            else:
                self.pending = (kind, ev.get("message", ""))
        self.show_rom()

    def show_rom(self):
        if self.rom is None:
            self.lb_rom.setText(tr("Nenhuma ROM de Mega Drive aberta na aba Extração. Abra uma lá para "
                                   "vincular ao jogo daqui, ou marque os jogos direto na lista."))
            for b in (self.bt_confirm, self.bt_link, self.bt_add_rom):
                b.setVisible(False)
            return
        r = self.rom
        gid, how = self.cat.find(r)
        desc = tr("ROM aberta: {0} (código {1}, checksum {2})").format(r["arquivo"], r["serial"], r["checksum"])
        self.suggest = None
        if gid and how in ("vinculo", "arquivo"):
            desc += " — " + tr("é o jogo: {0}").format(self.cat.by_id[gid]["nome"])
            self.bt_confirm.setVisible(False)
        elif gid:
            self.suggest = gid
            desc += " — " + tr("parece ser: {0}. Confirma?").format(self.cat.by_id[gid]["nome"])
            self.bt_confirm.setVisible(True)
        else:
            desc += " — " + tr("não reconheci o jogo: selecione-o na lista e clique em \"Vincular ao jogo "
                               "selecionado\".")
            self.bt_confirm.setVisible(False)
        self.lb_rom.setText(desc)
        self.bt_link.setVisible(True)
        self.bt_add_rom.setVisible(True)
        if gid:
            self.goto(gid)

    def confirm_suggestion(self):
        if self.suggest:
            self.link(self.suggest)

    def link_selected(self):
        gid = self.selected()
        if not gid:
            QMessageBox.information(self, tr("Catálogo"), tr("Selecione primeiro o jogo na lista."))
            return
        self.link(gid)

    def link(self, gid):
        if self.rom is None:
            return
        self.cat.link(gid, self.rom)
        if self.pending:
            self.cat.record(gid, self.pending[0], self.pending[1], self.rom)
            self.pending = None
        self._paint(gid)
        self.update_stats()
        self.show_rom()
        self.changed.emit()

    def add_for_rom(self):
        if self.rom is None:
            return
        name = Path(self.rom["arquivo"]).stem
        gid = self._ask_add(cat.clean_title(name) or self.rom.get("titulo", ""))
        if gid:
            self.link(gid)

    def add_game(self):
        self._ask_add("")

    def _ask_add(self, default):
        t, ok = QInputDialog.getText(self, tr("Adicionar jogo"), tr("Nome do jogo:"), text=default)
        if not ok or not t.strip():
            return None
        gid = self.cat.add_game(t)
        self.populate()
        self.goto(gid)
        return gid

    # ------------------------------------------------------------- CSV
    def export_csv(self):
        f, _ = QFileDialog.getSaveFileName(self, tr("Exportar CSV"),
                                           str(Path.home() / "compatibilidade_mega_drive.csv"), "CSV (*.csv)")
        if not f:
            return
        try:
            n = self.cat.export_csv(f, only_marked=not self.ck_all.isChecked())
        except OSError as e:
            QMessageBox.critical(self, tr("Exportar CSV"), str(e))
            return
        QMessageBox.information(self, tr("Exportar CSV"), tr(
            "{0} linha(s) gravada(s) em:\n{1}\n\nMande esse arquivo para quem mantém o conversor.").format(n, f))
