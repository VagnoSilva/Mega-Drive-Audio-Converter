"""Pergunta comum às abas Música VGM e Extração: gravar na pasta de saída ou numa pasta nova dentro
dela, e se cada música ganha uma pasta própria."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QInputDialog, QLineEdit, QMessageBox

from mdconv.i18n import tr

BAD = set('<>:"/\\|?*')


def ask_out_dir(parent, qsettings, dst: str, title: str):
    """Devolve (pasta, nome da pasta nova ou None, uma pasta por música) — ou None se cancelado.
    A pasta nova já sai criada."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(tr("Onde gravar os arquivos convertidos?"))
    box.setInformativeText(tr("Pasta de saída: {0}").format(dst))
    b_here = box.addButton(tr("Na pasta de saída"), QMessageBox.ButtonRole.AcceptRole)
    b_new = box.addButton(tr("Numa pasta nova dentro dela…"), QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(b_here)
    sub = QCheckBox(tr("Uma pasta para cada música (com os arquivos dela dentro)"))
    sub.setChecked(qsettings.value("vgm/subfolders", "true") in (True, "true", "1"))
    box.setCheckBox(sub)
    box.exec()
    per_song = sub.isChecked()
    qsettings.setValue("vgm/subfolders", "true" if per_song else "false")
    if box.clickedButton() == b_here:
        return dst, None, per_song
    if box.clickedButton() != b_new:
        return None
    name = ""
    while True:
        name, ok = QInputDialog.getText(parent, title, tr("Nome da pasta a criar dentro de\n{0}:").format(dst),
                                        QLineEdit.EchoMode.Normal, name)
        if not ok:
            return None
        name = name.strip().rstrip(".")
        if not name:
            QMessageBox.warning(parent, title, tr("Digite o nome da pasta."))
        elif any(c in BAD or ord(c) < 32 for c in name) or name in ("..", "."):
            QMessageBox.warning(parent, title, tr("O nome não pode ter estes caracteres: < > : \" / \\ | ? *"))
        else:
            break
    p = Path(dst) / name
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        QMessageBox.warning(parent, title, tr("Não consegui criar a pasta:\n{0}").format(exc))
        return None
    return str(p), name, per_song
