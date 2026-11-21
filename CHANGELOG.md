# Changelog

Todas as mudanças importantes do Mega Drive Audio Converter desde a versão 0.10.0.

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
