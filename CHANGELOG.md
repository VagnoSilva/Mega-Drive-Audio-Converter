# Changelog

Todas as mudanças importantes do Mega Drive Audio Converter desde a versão 0.10.0.

## [0.12.0] — 2026-11-25

### Novidades

- **Analisar ROM agora entende drivers no 68000** (Sonic 1 e parecidos): acha sozinha a rotina do driver
  que o VBlank chama (pela pilha no momento das notas) e o byte da RAM onde o jogo pede a música; na
  extração só o driver roda, quadro a quadro, sem o jogo atrapalhar.
- A análise aperta Start durante o boot (passa telas de título) e repete o mesmo boot na extração.
- Vinhetas curtas (sem loop) também entram na lista de músicas; a extração espera até 15 s pela primeira nota.
- Emulador: interrupção do HBlank (nível 4) do VDP — Michael Jackson's Moonwalker passa a rodar e tocar.
- Análise: reconhece jogos em que o 68000 segue a música e o Z80 só toca as notas (Mortal Kombat 1 e 2)
  e pedidos de música cujo valor "vazio" não é zero (Moonwalker).

- **Linux**: `packaging/build_linux.sh` gera o executável de Linux x64; `packaging/arch/PKGBUILD` instala
  no Arch com `makepkg -si` (atalho no menu e comando `megadrive-audio-converter`); o GitHub também passa
  a gerar o `MegaDriveAudioConverter-linux-x64.tar.gz`.

- **Outras versões da mesma ROM** (tradução, patch de região, revisão — checksum diferente): o conversor
  testa o perfil do jogo nela (toca duas músicas por alguns segundos) e, se funcionar, usa e guarda a
  aprovação em `versoes_testadas.json`, na pasta dos perfis. Se não funcionar, avisa e oferece a análise.

- **Pasta `perfis_megadrive/` no projeto**: os perfis de jogos vão junto com o conversor (Windows,
  Linux, AppImage, Arch), então quem baixar já extrai esses jogos; para contribuir, basta copiar o
  `.json` da análise para essa pasta. Já vem com Sonic 1, Mortal Kombat 1, 2, 3 e Ultimate 3,
  Moonwalker, Aladdin e Thunder Force III. Perfis na pasta do usuário têm prioridade.
- **Escolher os arquivos de cada música**: caixas XGM / VGM / Furnace (.fur) / Prévia (.wav) na aba
  Extração (Mega Drive, Neo Geo, arcades) e em Ferramentas → Música VGM; só o que estiver marcado é
  gerado (mais rápido e ocupa menos espaço). A escolha fica salva.
- **Aba "Catálogo Mega Drive"**: lista de 1.377 jogos (nome, franquia, região, tipo, se já tem perfil)
  com filtros por letra, tipo, franquia, situação e busca. Cada jogo tem uma bolinha: cinza (não
  testado), verde (funciona), vermelho (não funciona) e amarelo (analisado, falta ouvir); clique para
  trocar, botão direito para observação. A ROM aberta na Extração é reconhecida pelo vínculo salvo,
  pelo nome do arquivo ou sugerida pelo nome (confirmar), e pode ser vinculada à mão a qualquer jogo
  da lista (ou a um jogo novo). Abrir com perfil / analisar com sucesso / falhar atualizam a bolinha do
  jogo vinculado. **Exportar CSV** gera a planilha (com código do cartucho, checksum e erro da análise)
  para mandar a quem mantém o conversor. O catálogo é `perfis_megadrive/catalogo_megadrive.json`
  (gerado por `tools/make_catalog.py` a partir de uma pasta de ROMs); as marcações ficam na pasta do
  usuário.
- **Botão "Perfis do Mega Drive…"** (aba Extração): salvar o perfil do jogo carregado para compartilhar,
  exportar todos os seus perfis, importar um perfil recebido e abrir a pasta dos perfis — sem precisar
  achar a pasta do AppData/.config. Rodando do código-fonte, também copia direto para `perfis_megadrive/`.
- Análise: drivers que o jogo chama direto (`jsr` com o número do som em D0), como o de **Streets of
  Rage 2** (26 músicas); jogos que mandam o número da música junto com o programa do Z80, como
  **Altered Beast**. Perfis em `perfis_megadrive/` (também **Streets of Rage 1 e 3**).
- Análise: pedido na RAM que o driver reescreve todo quadro (Streets of Rage 1); entre vários pedidos
  diretos (música, efeitos, vozes), escolhe o que toca música (Streets of Rage 3); música da abertura
  com só 3 canais FM também conta.
- Análise: quando o título no cartucho é ilegível (alguns japoneses), usa o nome do arquivo da ROM.
- **Compilação**: `COMPILAR.md` explica Windows, Linux (AppImage, pasta, pacote do Arch) e GitHub;
  `build_windows.bat` confere o Python (64 bits, 3.10–3.13), usa ambiente separado, confere se as DLLs
  foram geradas dos fontes atuais (`tools/check_native.py`) e já sai com o zip para distribuir;
  `.gitattributes` fixa os finais de linha dos scripts (LF no Linux, CRLF no Windows).

### Correções

- Ferramentas — conversores e efeitos: a janela ficava mais alta que a tela e o botão **Converter**
  saía do alcance. Agora cada aba rola, o botão de ação fica fixo embaixo e a janela abre do tamanho da
  tela e pode ser redimensionada.
- O executável gerado pelo GitHub não levava a `md68k.dll` (extração de Mega Drive não funcionava nele);
  o workflow agora inclui a DLL e confere se ela está no pacote. Versão do pyproject atualizada para 0.12.0.

**Extração — jogos do Mega Drive**
- A aba Extração aceita **ROMs de Mega Drive** (.md, .bin, .gen, .smd ou .zip com uma delas). O 68000 do
  console é emulado (núcleo Musashi) e roda o **driver de som do próprio jogo**: o programa chama a
  inicialização, pede cada música e atualiza o som uma vez por quadro, como o jogo faz no VBlank. Tudo o
  que o jogo escreve no YM2612 e no PSG é gravado sem alteração — a música já é do Mega Drive, então não há
  conversão de chip nem perda de timbre.
- Primeiro jogo: **Street Fighter II' Special Champion Edition** (40 entradas, entre músicas, vinhetas e
  efeitos). O loop de cada música é achado sozinho (pelo comando de "goto" do driver).
- **Bateria e vozes (PCM)**: no XGM, as amostras originais do jogo vão para os canais PCM 1 e 2 (os canais
  3 e 4 ficam livres para o seu jogo). No VGM, o PCM sai misturado como o Z80 do jogo faz (dois canais
  somados, ~5,2 kHz), com o tempo exato do console.
- Segundo jogo: **Thunder Force IV** (48 músicas). O driver da Technosoft roda no **Z80**, agora emulado em C junto
  com o 68000 (com a janela de banco da ROM, a interrupção do VBlank e os timers do YM2612). O programa pede a
  música e liga o loop como o jogo faz; o loop é achado pelo fim de cada faixa.
- Terceiro jogo: **Sonic The Hedgehog 2** (músicas $81–$9F, SMPS no Z80).
- **Análise automática de ROM (experimental)**: ao abrir uma ROM de Mega Drive sem perfil, o programa oferece
  analisá-la. Ele liga o jogo no emulador até a música da abertura, descobre como o jogo pede música ao driver de
  som e testa todos os números, criando um perfil para você conferir pelas prévias. Funciona com jogos cujo som
  roda no Z80 (ex.: Sonic 2, Castlevania: Bloodlines, Aladdin; Thunder Force III só as músicas do primeiro banco).
  Alguns números podem ser variações ou ruído.
- **Bateria pelo Z80 no XGM** (Sonic 2 e outros jogos em que o Z80 escreve o DAC byte a byte): as batidas são
  separadas e agrupadas pelo começo da amostra, com uma taxa só por amostra. Na música $82 do Sonic 2: 5 amostras
  em vez de 63, sem nenhuma batida de fora.
- **Emulador do Mega Drive mais fiel**: o Z80 anda até o instante exato em que o 68000 pede ou devolve o
  barramento (o Sonic 1 travava na tela da SEGA), o bit do barramento lido em $A11100 diz se o Z80 está parado
  (o Gunstar Heroes travava), a região do console segue o cabeçalho da ROM (o Sonic Spinball japonês recusava o
  console de exportação) e o controle 1 existe (para "apertar Start"). Os 13 jogos testados tocam música.
- Saem XGM, VGM, .fur e prévia de cada música, como nas outras placas.
- ROM de Mega Drive sem perfil: o programa avisa o nome e o código do jogo (cada jogo precisa dos endereços
  do driver de som).

## [0.11.4] — 2026-11-22

### Novidades

**Ferramentas → Música VGM**
- **YM2610 (Neo Geo)**: VGM/VGZ de Neo Geo (vgmrips) entram na conversão direta. As escritas do chip e as amostras
  de ADPCM-A/B que vêm dentro do VGM (blocos 0x82 e 0x83) passam pela mesma conversão da extração da ROM:
  FM 1–4 → FM 1–4, SSG → PSG, ADPCM-A → PCM do XGM, ADPCM-B (com altura) → FM 5. Saem XGM, VGM, .fur e prévia.
  Serve para comparar o VGM gravado do MAME com a música tirada da ROM e para jogos de Neo Geo que a extração
  ainda não reconhece. Conferido com "Bloody (New Face Team)" do KOF '97. Avisos no registro para YM2610B (os
  canais FM extras ficam de fora), 2º chip e VGM sem as amostras.
- Botão **Opções do Neo Geo…**: as mesmas opções da aba Extração (canais PCM, chimbais no ruído, ADPCM-B no FM,
  duração e volume das amostras), salvas no mesmo lugar — mudar numa aba muda na outra. Ao lado, um resumo do
  que está valendo.
- **Tamanho do XGM** na própria aba (Qualidade máxima, Equilibrado, Compacto): o mesmo do menu Ferramentas →
  Tamanho do XGM, sincronizado com ele, e vale também para os VGZ de YM2610.
- **Pasta de destino**: ao clicar em Converter, o programa pergunta se grava na pasta de saída ou numa pasta nova
  dentro dela. Para a pasta nova, basta digitar o nome (nomes vazios ou com `< > : " / \ | ? *` são recusados).
- **Aba Extração com a mesma pergunta**: ao clicar em Extrair, escolhe entre a pasta de saída e uma pasta nova
  dentro dela (ex.: `saida\kof97`). Na pasta nova, as músicas, efeitos e vozes do jogo vão direto para ela (sem a
  pasta automática com o nome do jogo). Com "Uma pasta para cada música", cada música sai numa pasta com o nome
  dela (`saida\kof97\44_Musica_44\`), em vez de todas juntas em `musicas`.
- **Atenuar o PCM (DAC)**: de 0 a 24 dB, para quando a bateria e as vozes soam muito mais altas que a melodia. O
  ganho vai gravado nas amostras (o XGM não tem volume por canal PCM); FM e PSG não mudam. Vale para todos os chips
  da aba e fica salvo. O registro mostra "PCM (DAC) atenuado em N dB".
- **Uma pasta para cada música**: na mesma pergunta, a caixa "Uma pasta para cada música" (ligada por padrão, e o
  programa lembra a escolha) grava os 4 arquivos de cada música (XGM, VGM, .fur e prévia) numa pasta com o nome
  dela, dentro da pasta escolhida.

### Mudanças

- Opções do Neo Geo: o passo de volume de **3 dB saiu** (só aumentava a ROM e era o que estourava o limite de
  amostras). Ficam 6 dB (padrão) e 12 dB (menos ROM); opções salvas com 3 dB passam a valer 6 dB, na aba Extração
  e na conversão direta.

### Correções

**Extração — Neo Geo (KOF 94–98, Garou)**
- **Notas de ADPCM sumindo na segunda metade das músicas**: quando as variações de volume das amostras passavam
  do limite do XGM (63 amostras por música), as notas das amostras que aparecem por último ficavam de fora. No
  "Bloody (New Face Team)" do KOF '97 (música 44), com passo de 3 dB, sumiam 12 batidas entre 70 s e 98 s; no
  KOF '96, a música 36 perdia 14. Agora os volumes vizinhos da mesma amostra viram um só até caber (o registro
  mostra quantos), e todas as notas tocam. Encontrado comparando a extração da ROM com o VGZ do vgmrips: FM e
  escritas de ADPCM iguais nota a nota.

**Música VGM — VGM do Mega Drive**
- **PCM sem reamostrar quando a taxa já é ~14 kHz**: amostras de DAC a até 0,3% da taxa do XGM (ex.: 14 015 Hz dos
  VGMs do Furnace) passam byte a byte, em vez de interpoladas — a interpolação apagava agudos (diferença de até 69
  níveis por amostra). O registro mostra "cópia exata, sem reamostrar".

**Exportação**
- **Ida e volta VGM → XGM sem perder as amostras**: quando as amostras não se sobrepõem (um canal PCM, como nas
  músicas que vêm do Furnace), o VGM gerado guarda cada amostra uma vez só e cada disparo aponta para o começo
  dela, em vez de uma trilha misturada. Ao converter esse VGM de novo, as amostras originais voltam (antes viravam
  poucas amostras enormes, de até 15 s, que o modo Compacto cortava em 0,55 s — as vozes sumiam). O VGM também
  fica menor. Conferido: VGM → XGM → VGM → XGM com o PCM idêntico amostra a amostra.
- Leitura de VGM do Mega Drive: um salto do 0xE0 no banco começa outra amostra; disparos cortados usam a amostra
  inteira de que são o começo (e param no mesmo ponto); taxas medidas que diferem só alguns Hz contam como a mesma.
- **VGM com PCM mudo**: na conversão de VGM do Mega Drive, o VGM gerado não ligava o DAC ($2B = $80), então os
  players de VGM tocavam o FM 6 no lugar das amostras (vozes e bateria sumiam; o XGM e a prévia estavam certos).
  Agora o VGM liga o DAC no começo sempre que tem PCM.
- **Fim das músicas sem loop**: o desligamento final das notas mandava o key-off do FM 4 para um canal que não
  existe e nunca desligava o FM 6. Agora desliga os 6 canais.

### Diferenças conhecidas (extração da ROM × VGZ do vgmrips)

- Andamento 0,4% mais lento na extração da ROM (≈0,4 s em 100 s); os timers do YM2610 conferem com a conta do chip,
  a causa ainda não foi achada.
- O loop achado pela repetição das notas pode começar mais tarde que o do VGZ (Bloody: 26 s × 8 s), com o mesmo
  período: soa igual, só guarda alguns segundos a mais.

### Testes

- `tests/unit/test_neogeo.py`: leitura de VGM/VGZ de YM2610, conversão até o XGM com opções do Neo Geo, junção de
  volumes no limite de 63 amostras e passos de volume aceitos (6 e 12 dB).

## [0.11.3] — 2026-11-21

### Novidades

**Extração — Neo Geo**
- **Garou: Mark of the Wolves** (`garou.zip`): o programa de som (Sound Driver Ver 1.8 da SNK) roda emulado como
  nos KOF; 66 músicas e jingles. Precisa de um romset com a ROM M1 sem criptografia. 18 faixas já saem com nome:
  os temas dos 14 lutadores, Sunset Sky Again, Dark Lightning -Emergence-, Do You Do? e Victory Message.
- **Transbordo para o PSG** (Opções do Neo Geo…, desmarcado por padrão): quando o canal PCM está ocupado por uma
  nota que acabou de começar, a nota nova não some mais — vai imitada para um tom livre do PSG (onda quadrada na
  nota da amostra, bumbo e tons com a altura caindo, caixa no ruído se ele estiver livre). Os canais usados pelo
  SSG da música são respeitados; vozes e efeitos continuam no PCM. Tema do Dong Hwan (Garou) com um canal PCM:
  notas que sumiam 277 → 56, XGM 251 → 241 KB.

**Ferramentas → Música VGM**
- **Chips OPL — YM3812 (OPL2), YM3526 e Y8950** (arcades como City Streets e Street Smart, placas AdLib/Sound
  Blaster): os 9 canais FM de 2 operadores viram canais FM do YM2612 (par modulador → portadora; MULT, TL,
  feedback, KSR, KSL, tremolo/vibrato e envelopes convertidos, com a correção de taxa medida no emulador). Mais de
  6 vozes: as extras vão para o PSG como onda quadrada, com o envelope do OPL calculado quadro a quadro (as graves
  ficam no FM). Formas de onda do OPL2 aproximadas com o 2º par de operadores; modo bateria (bumbo/tom como
  vozes; caixa, prato e chimbal no ruído). Nova aba **OPL** na tabela de destinos para escolher à mão.
- **Estilo de referência** ("Copiar de"): deixa a música com as características técnicas de outra (XGM ou VGM do
  Mega Drive). Marque o que copiar: volume das amostras (um ganho só, pico igual ao da referência), volume do FM
  fixo (cada instrumento no volume mais forte que já usava), algoritmo e feedback de cada canal FM e LFO. "Ver"
  mostra o que a referência tem; o registro, o que mudou. Ex.: o menu de uma música extraída do Neo Geo com o
  jeito do intro de um jogo de Mega Drive.
- **Reduzir um XGM pronto**: abrir um XGM na aba e converter agora grava também um XGM novo, com a redução de
  tamanho escolhida — sem extrair de novo. Se cair na mesma pasta, ganha o modo no nome (ex.
  `30_compact.xgm`); o original nunca é sobrescrito. (Antes o XGM só era regravado quando o loop mudava.)

**Exportação**
- **Tamanho do XGM** (Ferramentas → Tamanho do XGM; na linha de comando, `--xgm-size max|balanced|compact`):
  escolha entre arquivo maior com mais qualidade ou menor com boa qualidade. Vale para XGM, VGM e prévia de todas
  as conversões e extrações (não para o .fur da música MIDI).
  - *Qualidade máxima*: só sai o que não muda o som — escritas que repetem o valor que o registrador já tem
    (FM e PSG; as de frequência só em par; key-on, DAC, timers e ruído nunca), amostras idênticas guardadas uma
    vez só, amostras que nenhuma nota toca e silêncio exato no fim das amostras.
  - *Equilibrado* (padrão): também o fim das amostras abaixo de −42 dB do pico e amostras quase iguais (mesma
    forma, até 1,5 dB de diferença).
  - *Compacto*: caudas abaixo de −30 dB (com descida de volume), nenhuma amostra acima de 0,55 s e cópias da
    mesma amostra em volumes diferentes (até 6 dB) juntadas.
  - O registro mostra o ganho. Ex.: tema do Terry (Garou) 409 KB → 398 / 392 / 257 KB; menu.xgm 35 → 29 KB;
    tema do Dong Hwan com as opções do Neo Geo ajustadas, 523 → 251 KB.

**Documentação**
- Manuais (português e inglês) e READMEs com as novidades acima e uma linha nova em Problemas comuns:
  "XGM grande demais".

### Correções
- VGM/VGZ de OPL (ex.: `03_City_Streets.vgz`) davam erro na aba Música VGM: o chip não era suportado.

## [0.11.2] — 2026-11-18

### Novidades

**Conversão de MIDI**
- **MIDI sem SoundFont**: o campo SoundFont da aba Conversão pode ficar vazio. Um MIDI comum (General MIDI)
  usa o banco General MIDI padrão do programa: kit de bateria com as 47 peças do GM e os 128 instrumentos GM
  como referência para os patches FM. Na linha de comando, `gm` no lugar do `.sf2`.
- **Verificação do SoundFont**: num MIDI General MIDI, se o kit de bateria do SF2 não servir (sem banco 128,
  amostras do Oki de um SF2 extraído da CPS1, peças faltando ou uma amostra só), ele é trocado pelo kit padrão e
  uma mensagem avisa. Ex.: um MIDI do Street Fighter Alpha com o SF2 do Street Fighter II não toca mais vozes e
  golpes dos lutadores no lugar da bateria.
- **Canais PSG de tom no arranjo** (aba Arranjo → "Canais PSG de tom: PSG1 PSG2 PSG3"): desmarque os tons do
  PSG que a música não deve usar — por exemplo, quando o transbordo para o PSG soa como chiado. As notas ficam no
  FM ou nos PSG marcados; o que não couber aparece no relatório. Salvo no projeto.

**Ferramentas → Música VGM**
- **VGM do Mega Drive → XGM / VGM / Furnace**: VGMs que já são do Mega Drive (YM2612 + SN76489 — rips,
  DefleMask, Furnace, SMPSPlay…) são reempacotados como faz o xgmtool: escritas no FM e no PSG por quadro, e as
  amostras do DAC (blocos comprimidos ou não, tocados por stream ou byte a byte) viram PCM do XGM no canal
  escolhido (CH1 a CH4).
- **XGM → VGM / Furnace**: um XGM pronto do SGDK vira VGM, .fur e prévia (no .fur, os canais PCM 3–4 vão para o
  PCM 2).
- **Pôr ou mudar o loop** em todas as conversões de VGM (AY, YM2203, Mega Drive e XGM): "Escolher: volta para …
  ao chegar a …"; o "ao chegar a" também corta a música.

**Interface e documentação**
- **Interface em inglês** (Ajuda → Idioma / Language): Português ou English. Na primeira vez segue o idioma do
  Windows; a troca vale ao reabrir o programa. Toda a janela é traduzida (menus, abas, botões, campos, dicas,
  mensagens e as subtelas Ferramentas, Canais dos efeitos e Opções do Neo Geo). O registro, os relatórios e as
  mensagens de erro do conversor continuam em português.
- **Manual em inglês** (`Manual_Mega_Drive_Audio_Converter_EN.pdf`) e **README em inglês** (`README.en.md`).

### Correções
- Furnace (.fur) de MIDIs com resolução alta (ex.: 480 ticks por semínima, comum nos MIDIs da internet): o
  "virtual tempo" saía fora do limite do Furnace (ex.: 3400/150) e a música não tocava na velocidade do XGM/VGM;
  mudanças de andamento eram cortadas em FF. Agora velocidade e virtual tempo são escolhidos dentro dos limites
  (1–255), com no máximo um tick por quadro, e os atrasos (EDxx) e cortes (ECxx) vão em ticks do Furnace.
- Aprovar um patch depois de trocar o SoundFont (mesma pasta de saída) não marcava o instrumento como aprovado:
  a aprovação usava os candidatos gerados com o SoundFont anterior. Agora só valem os candidatos do SoundFont
  atual; os antigos ficam de lado.
- Desligar todos os canais PSG de tom no arranjo dava o erro "min() iterable argument is empty" quando alguma
  parte ainda ia para o PSG (ex.: bateria sem PCM). Agora essas notas ficam fora do PSG e o relatório avisa.

## [0.11.1] — 2026-11-17

### Novidades
- **Neo Geo — The King of Fighters '94 a '98** (`kof94.zip` … `kof98.zip`, com o `neogeo.zip` na mesma pasta).
  O programa de som da ROM M1 roda num Z80 emulado com os timers do YM2610. O conversor reconhece sozinho as
  duas famílias de programa de som da SNK ("Ver 3.0 by MAKOTO" no 94/95 e "Sound Driver" 0.1/1.1/1.7 no
  96/97/98), acha a tabela de músicas e o comando que libera a música.
  - FM (4 canais) → FM 1–4, com a frequência recalculada para o clock do Mega Drive.
  - SSG → PSG; ADPCM-A → PCM do XGM (canais 1–2 por padrão, deixando 3–4 para as vozes do jogo).
  - Chimbais e pratos → ruído do PSG (opcional); ADPCM-B com altura → FM 5.
  - Novo botão **Opções do Neo Geo…** na aba Extração: canais PCM, duração máxima e passos de volume das
    amostras, corte dos ecos. O relatório mostra os KB de PCM de cada música.
  - O KOF 98 também é aceito com os nomes de arquivo `kof98_m1.rom`, `kof98_v1.rom`…
- **Nemesis (Konami GX400)**: a placa de som (Z80 + 2× AY-3-8910 + K005289) é emulada. Músicas: AY 2 → PSG
  (graves no FM 1–2) e as 2 vozes de tabela de onda do K005289 → FM 3 e 4. Efeitos (AY 1) → `.vgm` + `.c` para
  o SGDK.
- **Furnace (.fur) em todas as conversões diretas** (VGM de AY/YM2203, Moon Patrol, Kung-Fu Master, Lode Runner,
  Ghosts 'n Goblins, Nemesis, Neo Geo): cada música sai com o `.fur` ao lado do XGM e do VGM, com uma linha por
  quadro (60 por segundo) e o loop no mesmo ponto do XGM.
- **Menu Ferramentas** (Ctrl+T): a conversão de VGM, os efeitos de chip, os efeitos/vozes em WAV e o banco `.tfi`
  saíram do menu Arquivo e foram para uma subtela própria, com uma aba para cada um e as opções na tela.
- **Destino de cada canal do AY-3-8910** na conversão de VGM: Automático, PSG tom 1–3, FM 1–6 ou Omitir. O botão
  "Analisar o 1º arquivo" mostra para onde o automático mandaria cada canal.
- **Canais dos efeitos…** (Irem e Nemesis): cada efeito ganha os seus canais PSG/FM, salvo por jogo, e a tabela
  mostra com quais efeitos cada um divide canal.
- Efeitos de chip: os canais PSG e FM livres agora são escolhidos na tela (antes, fixos em PSG 1–2 e FM 4–5).

### Correções
- Moon Patrol: os efeitos saíam todos no tom 1 do PSG e se anulavam (o UFO voando calava o tiro; pulo e tiro
  brigavam). Novo padrão: UFO voando no PSG 1, tiro e moeda no PSG 2, pulo nos FM 4–5, planta no FM 3, explosão
  do UFO e passar ponto no FM 2.
- Um efeito sem tom do PSG livre vai inteiro para o FM, como onda quadrada (antes podia ficar mudo).
- Detecção de loop pela repetição das notas mais tolerante, para músicas em que algum contador do programa de
  som nunca se repete (ex.: KOF 96), sem confundir repetições só da bateria com o loop.

### Removido
- Exportação do banco de timbres `.opm` (VOPM) da extração CPS1. Os timbres do YM2151 continuam saindo
  convertidos na biblioteca de patches do Mega Drive.

## [0.11.0]

### Novidades
- **Conversão direta de VGM/VGZ com AY-3-8910**: tons → PSG (ou FM com onda quadrada, quando a parte desce
  abaixo de ~109 Hz ou vem de um 2º AY), ruído → ruído do PSG com a mesma taxa, envelope do AY recalculado quadro
  a quadro, loop do VGM preservado. Opção de travar o andamento na grade de 60 Hz.
- **Conversão direta de VGM com YM2203**: os timbres passam como estão, com a frequência e os envelopes
  corrigidos para o clock do Mega Drive; 2 chips = os 6 canais FM. O SSG vai para o PSG.
- **Efeitos de chip sem amostra**: VGM de AY → PSG/FM nos canais livres, com `.vgm`, prévia e um `.c` para o
  SGDK (`nome_play()` / `nome_update()` / `nome_stop()`).
- **Ghosts 'n Goblins** (Capcom, Z80 + 2× YM2203): a placa de som é emulada e cada comando vira música ou efeito.
  Conferido contra os VGMs do arcade: notas 93–99% iguais e loops idênticos.
- **Kung-Fu Master** (Irem M62): 5 músicas, 11 efeitos e 7 vozes digitalizadas (MSM5205 → WAV de 8 bits a 14 kHz).
- **Moon Patrol** e **Lode Runner** (Irem) direto da ROM: os scripts de som são interpretados. Moon Patrol
  conferido contra os VGMs do arcade (3 músicas idênticas, 2 com 95–98% das escritas iguais). Efeitos contínuos
  saem com loop e função `_stop()`.
- **Efeitos sonoros e vozes (WAV) para o SGDK**: WAV mono de 8 bits a 14 kHz sem o som "rouco" (anti-aliasing,
  remoção de DC, normalização, compressão leve opcional, corte de silêncio), prévia e tamanho na ROM.

### Correções e melhorias
- Loop dos VGMs convertidos: duração tirada do próprio VGM, marcador depois da inicialização (o xgmtool não
  perde o marcador) e emenda sem desligar as notas.
- Silêncio do início removido ("Começar na primeira nota", ligado por padrão) no VGM de AY e na conversão de
  MIDI; o loop acompanha o corte. Projetos salvos antes abrem com a opção desligada, para continuarem idênticos.

## [0.10.0]
- Aba **Extração**: música direto da ROM de arcades Capcom CPS1 e de jogos de Game Boy Advance (MP2k).
