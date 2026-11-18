"""Interface gráfica (PySide6).

O idioma é definido aqui, antes de os módulos da janela serem importados: alguns textos são
montados quando o módulo carrega (nomes das colunas, listas de destinos)."""


def _init_language() -> None:
    from mdconv import i18n
    try:
        from PySide6.QtCore import QSettings
        code = QSettings("mdconv", "Mega Drive Audio Converter").value("ui/language", "", type=str)
    except Exception:
        code = ""
    i18n.set_language(code or i18n.system_language())


_init_language()
