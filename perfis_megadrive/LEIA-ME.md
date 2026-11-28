# Perfis de jogos de Mega Drive

Cada `.json` desta pasta ensina o conversor a extrair as músicas de um jogo: onde fica o driver de
som, onde o jogo pede a música e quais números são músicas. Todo perfil daqui vai junto com o
conversor (inclusive no executável do Windows e no AppImage), então quem baixar já tem esses jogos.

## Como contribuir com um jogo novo

1. No conversor, aba **Extração**, abra a ROM. Se o jogo não tiver perfil, aceite a **análise
   automática**. O perfil é gravado na sua pasta de usuário:
   - Windows: `%APPDATA%\MegaDriveAudioConverter\perfis_megadrive`
   - Linux: `~/.config/MegaDriveAudioConverter/perfis_megadrive`
2. Ouça as prévias. Se quiser, apague da lista (`"songs"`) os números que não são música e dê nomes
   às faixas em `"names"` (ex.: `"names": {"129": "Green Hill"}` — o número em decimal).
3. Na aba **Extração**, botão **Perfis do Mega Drive…**:
   - **Salvar o perfil deste jogo para compartilhar…** grava o `.json` onde você quiser (ex.: Área de
     Trabalho); **Exportar todos os meus perfis…** copia todos de uma vez.
   - Mande o arquivo para quem mantém o conversor (ou faça um pull request colocando-o **nesta pasta**).
   - Quem recebe um perfil usa **Importar perfil que recebi…** (não precisa achar pasta nenhuma).

## Regras

- Um arquivo por versão de ROM. O conversor reconhece pelo código do cartucho e pelo checksum
  (`"serial"` e `"checksum"`); outras versões do mesmo jogo (tradução, patch de região) são testadas
  automaticamente e usam o perfil se ele funcionar.
- Se você tiver na sua pasta de usuário um perfil do mesmo jogo, o seu tem prioridade sobre o daqui.
- Não coloque ROMs aqui — só os `.json`.
- Em `"board"` diga se o perfil foi revisado à mão ("revisado") ou só criado pela análise.

## Catálogo de compatibilidade

`catalogo_megadrive.json` é a lista de jogos da aba **Catálogo Mega Drive** do conversor (onde cada
pessoa marca verde/vermelho e exporta um CSV). Para gerar de novo a partir de uma pasta de ROMs com
nomes No-Intro: `python tools/make_catalog.py "caminho/da/pasta"`.
