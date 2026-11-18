# Changelog

Todas as mudanças importantes do Mega Drive Audio Converter desde a versão 0.10.0.

## [0.11.2] — 2026-11-18

### Novidades
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
