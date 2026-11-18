"""Idioma da interface: português (o texto do código) ou inglês.

O texto em português é a chave: ``tr("Abrir música")`` devolve "Open song" com o idioma em
inglês e o próprio texto em português caso contrário — ou quando a frase ainda não tem
tradução, para nada ficar em branco. Textos com partes variáveis usam ``tr("… {0} …").format(x)``.

O idioma é escolhido no menu Ajuda → Idioma e guardado nas configurações do programa; na
primeira vez segue o idioma do sistema (português → português; qualquer outro → inglês). A
troca vale ao reabrir o programa: os textos são lidos quando cada janela é montada.

O registro (log), os relatórios e as mensagens de erro vindas do conversor ainda saem em
português.
"""
from __future__ import annotations

LANGUAGES = {"pt": "Português", "en": "English"}
_lang = "pt"
_catalog: dict[str, str] | None = None


def set_language(code: str) -> None:
    global _lang
    _lang = code if code in LANGUAGES else "pt"


def language() -> str:
    return _lang


def system_language() -> str:
    """Idioma inicial: o do sistema operacional."""
    try:
        from PySide6.QtCore import QLocale
        name = QLocale.system().name()
    except Exception:                       # sem Qt (linha de comando, testes)
        import locale
        name = (locale.getlocale()[0] or "")
    return "pt" if name.lower().startswith("pt") else "en"


def _en() -> dict[str, str]:
    global _catalog
    if _catalog is None:
        from mdconv.i18n_en import EN
        _catalog = EN
    return _catalog


def tr(text: str) -> str:
    if _lang == "pt" or not text:
        return text
    return _en().get(text, text)
