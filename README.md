# Mega Drive Audio Converter — versão 0.12.0

*Read in English: [README.en.md](README.en.md)*

Conversor de músicas (MIDI + SoundFont) para o som do Mega Drive (YM2612 + SN76489 + PCM),
com exportação XGM v1 (SGDK), VGM e Furnace. Interface em português e inglês (Ajuda → Idioma /
Language).

A janela tem duas abas:
- **Extração** — tira MIDI + SoundFont direto da ROM: arcades **Capcom CPS1** (.zip do MAME) e
  **Game Boy Advance** (.gba, motor de som da Nintendo). Moon Patrol, Kung-Fu Master, Lode Runner,
  Ghosts 'n Goblins, Nemesis, KOF 94 a 98 e Garou (Neo Geo) saem direto para o Mega Drive (XGM/VGM e efeitos em código C).
  Jogos do próprio **Mega Drive** (.md/.bin; com perfil: Street Fighter II' SCE, Thunder Force IV e Sonic 2) saem
  com o driver de som do jogo rodando no 68000 ou no Z80 emulados; para os outros, a extração oferece uma
  análise automática que monta o perfil. Na CPS1 os timbres FM originais (YM2151)
  já saem convertidos para YM2612 numa biblioteca de patches aprovados. O botão
  "Abrir na conversão" leva a música escolhida para a outra aba.
- **Conversão** — o conversor de sempre. Também aceita MIDI + SF2 do GBA Mus Ripper.

Menu **Ferramentas → Conversores e efeitos…** (Ctrl+T) abre uma subtela com os conversores que
ficam fora do fluxo MIDI + SF2, um por aba:
- **Música VGM (AY / YM2203)** — para arcades e computadores com o chip AY-3-8910 (Moon Patrol,
  Kung-Fu Master, MSX, ZX Spectrum…) ou YM2203 (Ghosts 'n Goblins e outros arcades OPN). Os chips
  são parentes dos do Mega Drive, então a música é traduzida registrador a registrador (sem MIDI)
  e sai em XGM, VGM e prévia WAV. Uma tabela escolhe o destino de cada canal do AY (automático,
  PSG tom 1–3, FM 1–6 ou omitir). Código em `mdconv/ay/`, `mdconv/opn/` e `mdconv/vgmconv.py`.
- **Efeitos de chip (AY)** — efeito em VGM de AY → PSG/FM + código C para o SGDK, nos canais que
  você marcar como livres.
- **Efeitos e vozes (WAV)** — PCM de 8 bits na taxa do driver.
- **Banco de patches .tfi** — junta patches .tfi num banco para os candidatos FM.

O código da extração fica em `mdconv/extract/` (o mesmo do extrator independente da pasta
`cps1_extractor`, versão em `mdconv/extract/__init__.py`).

A versão fica em `mdconv/__init__.py` (mostrada no título da janela, em Ajuda → Sobre e em
`python -m mdconv.cli --version`).

## Histórico de versões

**0.12.0**
- **Extração de jogos do Mega Drive** (`mdconv/megadrive.py`): o 68000 é emulado pelo **Musashi**
  (`mdconv/chips/native/md68k/`, MIT) com um mapa de memória mínimo do console (`mdsnd.c`: ROM, RAM,
  área do Z80, YM2612, PSG, BUSREQ/RESET e o status do VDP). As rotinas de som da ROM são chamadas como
  o jogo faz (inicialização, "tocar música" com o número em D0, atualização a cada quadro de 59,92 Hz) e
  tudo o que vai para o YM2612 e o PSG é gravado como está. O loop vem de um breakpoint no comando de
  "goto" do driver. Cada jogo tem um perfil (`megadrive.PROFILES`); o primeiro é o **Street Fighter II'
  Special Champion Edition** (40 entradas). O PCM do Z80 é reproduzido pelo perfil (`megadrive.Mix2`: 2
  canais somados e divididos por 2, ~5,2 kHz): no XGM as amostras originais vão para os canais PCM 1–2;
  no VGM, o DAC misturado como no console.
- **Z80 em C** (`mdconv/chips/native/md68k/z80.c`): tradução do `mdconv.z80` com os T-states reais, conferida
  instrução a instrução contra o Z80 em Python (código aleatório, com interrupções). Roda junto com o 68000
  (relógio mestre: 7 ciclos por ciclo do 68000, 15 por ciclo do Z80), com BUSREQ/RESET, a janela de banco em
  $8000, a interrupção do VBlank e os **timers A e B do YM2612** (o status lido em $4000 traz os flags).
  Breakpoints no Z80 registram o IX (qual faixa voltou ao loop).
- Perfis com o driver no Z80 (`driver="z80"`): segundo jogo, **Thunder Force IV** (48 músicas, driver da
  Technosoft). O loop vem do breakpoint no fim da faixa e do ponteiro da lista de padrões (uma passada basta).
- **Jogo inteiro no emulador** (`Machine.run_game`): do reset, com a interrupção do VBlank (nível 6, linha 224) e o
  status/contador HV do VDP pelo relógio. Estado salvo (`save_state`/`load_state`) e troca de escritas do 68000
  na RAM do Z80 (`set_zoverride`).
- **Análise automática** (`mdconv/mdanalyze.py`, perguntada na aba Extração quando a ROM não tem perfil): liga o
  jogo até a música da abertura, acha a escrita do 68000 na RAM do Z80 que escolhe a música e testa os 256
  valores. Gera um perfil `driver="boot"` em `%APPDATA%/MegaDriveAudioConverter/perfis_megadrive/`. Na extração,
  o jogo roda até o pedido, o número é trocado e o Z80 toca sozinho; o loop vem da repetição das notas. Sonic 2
  já vem com o perfil assim (`sonic2`). Experimental: drivers com pedidos de vários bytes (MK II, Road Rash),
  música em outro banco (Thunder Force III) e o GEMS (Aladdin) ainda dão resultados incompletos.
- `mdvgm.convert`: um disparo de PCM pode trazer o próprio canal (4º campo).
- `md68k.dll` gerada por `tools/build_dll.sh` (clang + lld, só importa malloc/free/memcpy/memset/sin/cos
  do msvcrt.dll); no Linux a biblioteca é compilada na hora com gcc.

**0.11.4**
- **Neo Geo (YM2610) na aba Música VGM**: VGM/VGZ do vgmrips passam pela mesma conversão da extração
  da ROM (`neogeo.read_vgm` → `neogeo.to_stream`), com as amostras de ADPCM-A/B que vêm no VGM (blocos
  0x82/0x83). Botão "Opções do Neo Geo…" (as mesmas da aba Extração) e "Tamanho do XGM" na própria aba.
- **Pasta de destino**: ao converter, escolhe entre a pasta de saída ou uma pasta nova dentro dela.
- **Correção — notas de ADPCM sumindo** (KOF 94–98, Garou): no limite de 63 amostras do XGM, volumes
  vizinhos da mesma amostra viram um só (`neogeo.fit_sample_slots`) em vez de as últimas amostras
  ficarem de fora (Bloody/KOF '97: 12 batidas a menos entre 70 e 98 s).
- Passo de volume de 3 dB removido das Opções do Neo Geo (ficam 6 e 12 dB).

**0.11.3**
- **Neo Geo — Garou: Mark of the Wolves** (`garou.zip`): o programa de som é o "Sound Driver(ROM)
  Ver 1.8" da SNK, da mesma família do KOF 96–98 — reconhecido pela mesma tabela de comandos (66
  músicas e jingles). Precisa de um romset com a ROM M1 sem criptografia.
- **OPL — YM3812, YM3526, Y8950** na aba Música VGM (`mdconv/opl/convert.py`): 9 canais FM de 2
  operadores → 6 FM do YM2612 (algoritmo 4/7, envelopes corrigidos: o EG do OPL anda a cada amostra,
  o do YM2612 a cada 3) + PSG para as vozes que sobram; ondas do OPL2 com o par 3→4; modo bateria;
  rotas por canal na aba OPL da tabela de destinos.
- **Tamanho do XGM** (`mdconv/export/optimize.py`; menu Ferramentas → Tamanho do XGM, ou
  `--xgm-size max|balanced|compact`): antes de gravar XGM/VGM/prévia, remove escritas repetidas
  (estado esquecido no quadro do loop; frequência só em par), junta amostras idênticas e — conforme
  o modo — corta caudas (−42 / −30 dB), limita amostras a 0,55 s e junta cópias em volumes
  diferentes. Garou, tema do Terry: 409 → 392 KB (Equilibrado) → 257 KB (Compacto).
- **Transbordo para o PSG** no Neo Geo (`Options.psg_overflow`, `neogeo._Overflow`): nota de ADPCM
  que cortaria outra recém-começada no mesmo canal PCM vira tom/bumbo/caixa no PSG (canais livres do
  SSG, altura por autocorrelação, volume pelo RMS por quadro). Amostras que nenhuma nota toca saem
  do XGM (`export/optimize.py`).
- **Estilo de referência** (`mdconv/export/restyle.py`; Ferramentas → Música VGM → "Copiar de"):
  copia de outro XGM/VGM do Mega Drive o pico das amostras, o volume fixo do FM (portadoras no TL
  mais baixo visto com o mesmo patch, moduladores no mais comum), algoritmo/feedback por canal e o
  LFO ($22 e AMS/FMS de $B4). `convert_file(..., style=(referência, opções))`.
- Abrir um XGM na aba Música VGM grava também um XGM novo, reduzido (nunca sobrescreve o
  original).

**0.11.2**
- **VGM do Mega Drive** (YM2612 + SN76489) na aba Música VGM das Ferramentas: reempacotado para
  XGM/VGM/.fur (`mdconv/mdvgm.py`), com as amostras do DAC — blocos de dados tipo 0x00 e
  comprimidos 0x40 (n bits e DPCM, tabela 0x7F), streams 0x90–0x95 e escrita direta 0x80/0xE0 —
  virando PCM do XGM no canal escolhido. A mesma aba aceita XGM pronto (→ VGM, .fur e prévia). Loop escolhido à mão (`loop_start`/`loop_end`) em todas
  as conversões de VGM.
- **Interface em inglês**: menu Ajuda → Idioma / Language (Português ou English; na primeira vez
  segue o idioma do Windows; vale ao reabrir). Os textos da janela passam por `tr()`
  (`mdconv/i18n.py`) e a tradução fica em `mdconv/i18n_en.py`; o registro, os relatórios e as
  mensagens de erro do conversor continuam em português. Manual em inglês
  (`Manual_Mega_Drive_Audio_Converter_EN.pdf`) e `README.en.md`.
- Furnace (.fur) a partir de MIDI: a velocidade e o "virtual tempo" passam a ficar dentro dos
  limites do Furnace (1–255) com qualquer resolução do MIDI. Antes, num MIDI de 480 ticks por
  semínima o virtual tempo saía 3400/150 e o .fur não tocava na velocidade do XGM/VGM.
- **Canais PSG de tom no arranjo**: caixas PSG1, PSG2 e PSG3 na aba Arranjo
  (`ProjectSettings.psg_channels`). Os tons desmarcados ficam mudos (ou livres para o jogo);
  sem nenhum, nenhuma parte vai para o PSG de tom nem transborda para ele.
- **MIDI sem SoundFont**: o campo SoundFont da aba Conversão pode ficar vazio. Um MIDI comum
  (General MIDI) usa o banco General MIDI padrão do programa (`mdconv/io_sf2/gm.py`, sintetizado na
  primeira vez em `~/.mdconv/gm_padrao_v1.sf2`): kit de bateria com as 47 peças do GM no banco 128 e
  os 128 programas GM no banco 0, como formas de onda simples por família (referência para os
  patches FM, aprovados por programa GM). Na linha de comando, use `gm` no lugar do .sf2.
- **Verificação do SoundFont**: num MIDI General MIDI, se o kit de bateria do SF2 não servir (sem
  banco 128, amostras do Oki de um SF2 extraído da CPS1 — bateria misturada com vozes —, peças
  faltando ou uma amostra só para todas as peças), o kit é trocado pelo kit padrão e uma mensagem
  avisa; os outros instrumentos continuam vindo do SF2. Se os instrumentos também forem de um jogo
  extraído, a mensagem sugere deixar o campo vazio. MIDIs da aba Extração e do GBA Mus Ripper não
  passam pela verificação.
- Aprovar um patch depois de trocar o SoundFont (mesma pasta de saída) agora marca o instrumento
  como aprovado: só valem os candidatos gerados com o SoundFont atual.
- Desligar todos os canais PSG de tom não dá mais erro quando alguma parte ia para o PSG
  (ex.: bateria sem PCM).

**0.11.1**
- Menu Arquivo mais enxuto: a conversão de VGM, os efeitos de chip, os efeitos WAV e o banco .tfi
  foram para uma subtela própria (Ferramentas → Conversores e efeitos…, Ctrl+T), com uma aba para
  cada um e as opções na tela, em vez de uma série de perguntas.
- Conversão direta de VGM com AY-3-8910: escolha do destino de cada canal (automático, PSG tom 1–3,
  FM 1–6 ou omitir), por exemplo para mandar o baixo para o FM mesmo cabendo no PSG. O botão
  "Analisar o 1º arquivo" mostra para onde o automático mandaria cada canal.
- Efeitos de chip: os canais PSG e FM livres são escolhidos na tela (antes, fixos em PSG 1–2 e FM 4–5).
- Extração dos jogos Irem (Moon Patrol, Kung-Fu Master, Lode Runner): botão "Canais dos efeitos…"
  escolhe os canais do PSG e do FM de cada efeito (salvo por jogo). Um efeito sem tom do PSG livre
  vai inteiro para o FM, como onda quadrada. No Moon Patrol, os efeitos que tocam juntos saíam
  todos no tom 1 do PSG e se anulavam (o UFO voando, contínuo, calava o tiro; pulo e tiro
  brigavam); o padrão agora separa: UFO voando no PSG 1, tiro e moeda no PSG 2, pulo nos FM 4–5,
  planta no FM 3, explosão do UFO e passar ponto no FM 2.
- Extração do **Nemesis** (Konami Bubble System / GX400: Z80 + 2× AY-3-8910 + K005289), com o
  `nemesis.zip` do MAME: a placa de som é emulada (`mdconv/konami.py`, mapa de memória do MAME) e
  cada comando vira música (XGM, VGM e prévia) ou efeito (.vgm + .c para o SGDK). Nas músicas, o
  AY 2 vai para o PSG (partes graves no FM 1–2) e as 2 vozes de tabela de onda do K005289 para os
  FM 3 e 4, um patch de 4 portadoras com os harmônicos mais fortes de cada forma de onda. O loop
  sai do estado repetido da placa ou, quando o andamento oscila, da repetição das notas. Os
  efeitos (AY 1) usam por padrão o FM 5; a tabela "Canais dos efeitos…" também vale para ele.
- Extração do **Neo Geo**: **KOF 94 a 98** (`kof94.zip` … `kof98.zip`; o KOF 98 também com os nomes
  de arquivo `kof98_m1.rom`/`kof98_v1.rom`… dos pacotes fora do padrão do MAME). O
  programa de som da ROM M1 roda no Z80 emulado com os timers do YM2610 (`mdconv/neogeo.py`); as
  músicas são os comandos do tipo 2 da tabela do próprio programa, achada sozinha no código de dois
  famílias de programa de som da SNK ("Ver 3.0 by MAKOTO", do 94/95, e "Sound Driver" 0.1/1.1/1.7,
  do 96/97/98);
  o comando que libera a música também é achado sozinho. O loop vem do estado repetido da placa ou,
  quando algum contador nunca se repete (KOF 96), da repetição das notas. Conversão: FM 4 canais → FM 1–4 (frequência recalculada para
  o clock do Mega Drive), SSG → PSG, ADPCM-A → PCM do XGM (canais 1–2 por padrão, deixando 3–4
  para as vozes e golpes do jogo; volume gravado na amostra, normalizado pela nota mais forte),
  chimbais e pratos → ruído do PSG, ADPCM-B (instrumento com altura) → FM 5 com a altura medida na
  própria amostra. "Opções do Neo Geo…" na aba Extração regula canais e tamanho das amostras; o
  relatório mostra os KB de PCM de cada música.
- **Furnace (.fur) também nas conversões diretas** (VGM de AY/YM2203, Moon Patrol, Kung-Fu Master,
  Lode Runner, Ghosts 'n Goblins, Nemesis, Neo Geo): cada música sai com o `.fur` ao lado do XGM e do
  VGM (`mdconv/export/fur_stream.py`). O módulo é montado a partir das escritas já convertidas para o
  Mega Drive (YM2612 DualPCM + SN76489), uma linha por quadro: cada key-on do FM vira nota com o
  timbre do canal naquele instante como instrumento, volume na coluna de volume, ajustes de altura
  em E5xx (ou nova nota em legato), PSG e ruído quadro a quadro, amostras PCM num instrumento de
  bateria (uma tecla por amostra) e o loop com 0Bxx.
- Removida a exportação do banco de timbres .opm (VOPM) da extração CPS1, que não era usada: os
  timbres do YM2151 continuam saindo convertidos na biblioteca de patches do Mega Drive.

**0.11.0**
- Conversão direta de VGM/VGZ com AY-3-8910 (menu Arquivo): tons → PSG (ou FM com onda
  quadrada, quando a parte desce abaixo de ~109 Hz ou vem de um 2º AY), ruído → ruído do PSG com
  a mesma taxa do AY (cadenciado pelo tom 3), envelope de hardware do AY recalculado quadro a
  quadro, loop do VGM preservado. Opção de travar o andamento na grade de 60 Hz.
- Conversão direta de VGM com **YM2203** (OPN): os timbres passam como estão (mesma família do
  YM2612), com a frequência recalculada para o clock do Mega Drive e as taxas de envelope corrigidas
  pela diferença de taxa interna; 2 chips = os 6 canais FM do YM2612. O SSG (onda quadrada) vai
  para o PSG pelo conversor do AY. O VGM gerado mantém o tempo exato de cada comando.
- Efeitos de chip sem amostra (menu Arquivo → "Converter efeito de chip (VGM de AY) para PSG/FM +
  código C…"): o efeito passa pelo conversor do
  AY e fica preso aos canais livres (PSG 1–2, FM 4–5; tons graves demais para o PSG vão para o FM
  como onda quadrada). Sai .vgm, prévia e um .c para o SGDK com `nome_play()`/`nome_update()`.
  Ghosts 'n Goblins (Capcom, Z80 + 2× YM2203): ninguém documentou o formato das partituras, então a
  placa de som é emulada (`mdconv/z80.py` + `mdconv/capcom_opn.py`, memória e interrupção iguais às
  do MAME): o programa original roda, recebe cada comando de som (01–3F) e o que ele escreve nos
  YM2203 vira VGM → conversor do YM2203 (músicas) ou efeito de chip (sons só de SSG). O loop é
  achado sozinho (estado repetido da placa ou repetição das notas). Conferido contra os VGMs do
  arcade: notas 93–99% iguais e loops idênticos (32,0 s, 19,2 s, 25,6 s…). Abra o gng.zip na aba
  Extração; a extração completa leva alguns minutos.
  Kung-Fu Master (Irem M62) usa a mesma linguagem, com um comando diferente; abrindo o kungfum.zip
  na aba Extração saem as 5 músicas, 11 efeitos e as 7 vozes digitalizadas (ADPCM do MSM5205
  decodificado → WAV de 8 bits a 14 kHz). Conferido contra os VGMs do arcade: 94–99% das escritas
  iguais.
  Para o Moon Patrol (aba Extração: abra o mpatrol.zip), as 5 músicas (XGM, VGM e prévia, como a conversão de VGM) e os efeitos saem
  direto da ROM (pastas musicas e efeitos): os scripts de som da ROM (mp-s1.1a, no mpatrol.zip) são interpretados
  (`mdconv/mpsl.py`, a partir da desmontagem de computerarcheology.com) — exatamente o que o jogo
  escreve nos AY. Conferido contra os VGMs gravados do arcade: 3 músicas idênticas e as outras 2
  com 95–98% das escritas iguais. Efeitos contínuos (planta espacial, UFO voando) saem com loop e
  função `_stop()`. Código em `mdconv/chipsfx.py`.
- Efeitos sonoros e vozes (menu Arquivo → "Preparar efeitos sonoros e vozes para o SGDK (WAV)…"):
  WAV de qualquer taxa/resolução → WAV mono de 8 bits na taxa do driver (14.000 Hz = XGM v1), sem
  o som "rouco": filtro anti-aliasing antes de reamostrar, remoção de DC, volume normalizado (e
  compressão leve opcional) para usar os 256 níveis, silêncio do começo e do fim cortado, prévia
  para ouvir no PC e relatório com o tamanho na ROM. Opção de manter o volume relativo entre os
  arquivos. Código em `mdconv/sfx.py`.
- Loop dos VGMs convertidos: a duração vem do próprio VGM (quadros inteiros que cabem entre o
  ponto de loop e o fim), o marcador de loop fica depois da inicialização (patches e mudos não
  são refeitos a cada volta, e o xgmtool não perde o marcador) e a emenda não desliga as notas.
- Silêncio do início removido: a música começa na primeira nota, no VGM de AY e na conversão
  de MIDI (aba Arranjo → "Começar na primeira nota", ligado por padrão; os MIDIs do GBA Mus
  Ripper têm 1 compasso mudo no começo). O loop acompanha o corte. Projetos salvos antes desta
  versão abrem com a opção desligada, para continuarem idênticos.

**0.10.0**
- Extrator embutido: aba **Extração** (CPS1 e GBA) ao lado da aba **Conversão**. Depois de
  extrair, "Abrir na conversão" preenche MIDI, SoundFont e, na CPS1, a biblioteca de patches do jogo.
- GBA direto da ROM: mesmo resultado do GBA Mus Ripper (numeração das músicas, bancos, nomes no
  SoundFont, curva de força e volume), então os patches aprovados e a classificação de bateria/PSG
  continuam iguais.

**0.9.3**
- CPS2 (QSound) retirado das origens suportadas: nos testes o resultado não ficou bom. CPS1 continua.

**0.9.2**
- Histórico de decisões: cada candidato aprovado, recusado (botão novo "✖ Recusar") ou ouvido fica
  registrado em `%USERPROFILE%\.mdconv\decisoes.jsonl`, com tipo e papel do instrumento, opções
  da busca, origem do candidato e custos medidos — base para, no futuro, ajustar a ordem dos
  candidatos, as opções de busca por tipo de instrumento e a medida de semelhança ao seu ouvido.
  Resumo em Arquivo → "Histórico de decisões…".
- Candidatos recusados aparecem marcados e o rascunho automático não os escolhe.

**0.9.1**
- Patches travados por música: ao salvar o projeto (`.mdcproj`), os patches FM usados ficam
  gravados nele; aprovar patches em outras músicas não muda mais esta. Ao abrir, o programa avisa se
  a biblioteca tem versões diferentes e pergunta se quer atualizar (menu Arquivo também tem
  "Atualizar patches travados" e "Destravar patches desta música").
- Sugestões da biblioteca: ao gerar candidatos, os patches já aprovados para sons parecidos (de
  qualquer música/jogo) entram como candidatos, como estão e refinados.
- Rascunho automático: gera e aprova o melhor candidato de todos os instrumentos FM sem patch,
  marcados como "rascunho" para revisão.
- Furnace: andamento correto para MIDIs com qualquer resolução (antes só 24 ticks por semínima;
  os da CPS1 usam 48). Os do GBA saem idênticos à versão anterior.
- Banco de patches .tfi vazio não causa mais erro ao gerar candidatos.

**0.9.0** — versão anterior (GBA).

## Planejado

- Konami (geração de *Turtles in Time*: YM2151 + K053260) na aba Extração.

## Requisitos

- Python 3.10 ou mais novo
- numpy
- pytest (para os testes)

- scipy (reamostragem e otimização)
- no Linux, gcc (os emuladores em C são compilados na primeira execução); no Windows as DLLs vão prontas

Os leitores MIDI/SF2 e o emulador YM2612/SN76489 são próprios.

## Uso

```
python -m mdconv.cli analyze musica.mid soundbank.sf2 --json relatorio.json
```

Mostra, por parte (faixa × canal): programa, número de notas, faixa de notas, polifonia,
fração do tempo ativa, pitch bend, CCs e parâmetros RPN/NRPN decodificados; andamento e
mudanças; loops (marcadores `loopStart`/`loopEnd`, CC111, EMIDI ou repetição de
compassos); e a correspondência de cada instrumento com o preset, as zonas e os samples
do SF2, com o tipo detectado (tonal, kit, CGB quadrada, CGB onda).

### Renderizar a música original (referência para o A/B)

```
python -m mdconv.cli render-song musica.mid soundbank.sf2 original.wav
python -m mdconv.cli render-song musica.mid soundbank.sf2 so_baixo.wav --solo 4,13
python -m mdconv.cli render-song musica.mid soundbank.sf2 aspero.wav --interp nearest
```

Opções: `--law m4a|gm` (lei de volume; m4a = linear, padrão), `--no-vibrato`,
`--interp nearest` (aproxima o som "áspero" do mixer do GBA; não é emulação do hardware).

### Renderizar e medir cada instrumento

```
python -m mdconv.cli render-instruments musica.mid soundbank.sf2 pasta_saida
```

Gera um WAV por nota representativa de cada instrumento usado (grave, média e aguda; cada
peça nos kits) e `caracteristicas.txt` / `.json` com: altura medida e desvio em cents,
ataque, sustentação, decaimento, release, fração harmônica, brilho e vibrato.

### O que é aproximado (e precisa ser conferido pelo ouvido)

- Vibrato do m4a: frequência = LFOS × 60/256 Hz, profundidade = MOD × 100/256 cents.
- Lei de volume do m4a: velocity × volume, linear.
- Reverb/chorus dos players MIDI não são aplicados (o GBA não os tem).

### Gerar patches FM para os instrumentos (com prévias A/B)

```
python -m mdconv.cli fm-fit musica.mid soundbank.sf2 pasta_saida [--budget quick|normal|thorough]
```

Para cada instrumento tonal: busca de patches YM2612 com o emulador no laço, e um WAV
por candidato que toca, nota a nota, o ORIGINAL e depois o FM. `previas.txt` resume
custos e parâmetros; `patches.json` guarda os patches. Kits de bateria vão para PCM.

### Referência local: Genesis Plus GX (opcional, não distribuído)

O núcleo YM2612 do Genesis Plus GX é mais fiel ao hardware e é usado, se disponível,
como motor das prévias e nos testes de validação. Sua licença não permite distribuí-lo
com o projeto, então cada usuário compila o próprio: copie `ym2612.c` e `ym2612.h` do
Genesis Plus GX para `tools/gpgx_reference/`, rode `build_windows.bat` e defina
`MDCONV_GPGX_LIB` como o script indica.

## Testes

```
python -m pytest
```

Testes com os arquivos reais do jogo (fora do repositório):

```
set MDCONV_REAL_DIR=C:\caminho\dos\arquivos
python -m pytest
```

Os testes geram MIDI e SF2 sintéticos com conteúdo conhecido (`tests/fixtures/builders.py`).

## Organização

```
mdconv/
  io_midi/   smf.py (leitor SMF), tempo.py (mapa de andamento), song_builder.py (notas, CCs, RPN/NRPN)
  io_sf2/    sf2.py (leitor SF2), resolve.py (banco/programa/nota/velocity → vozes)
  model/     song.py (modelo de dados)
  analysis/  midi_stats.py, instrument_match.py, report.py, features.py
  reference/ sampler.py (sampler SF2), instruments.py (render + medição por instrumento)
  chips/     native/mdchips.c (YM2612 + SN76489), emulator.py, reference_gpgx.py (validação local)
  fm/        patch.py, metrics.py, fitter.py (busca), instrument_fit.py (prévias A/B)
  library/   patch_library.py (biblioteca reutilizável)
  cli.py
tests/
```

## Regras de interpretação

As decisões que afetam o resultado musical estão documentadas no topo de
`io_midi/song_builder.py` e `io_sf2/resolve.py`. Resumo:

- Nenhuma nota é descartada. Sobreposições, notas sem note-off e note-offs órfãos
  viram avisos no relatório.
- O Program Change nunca é tratado como índice do preset: a busca é por (banco, programa).
- O canal 10 só vira bateria GM em MIDI que não venha do ripper; no m4a qualquer canal
  pode ter kit.

## Licença

GPLv3 proposta, pendente de confirmação.

## Fase 6 — Exportação XGM v1 e VGM

```
python -m mdconv.cli export musica.mid banco.sf2 pasta_saida --library biblioteca_patches [--format xgm,vgm] [--wav]
```

* **XGM v1** (para o SGDK): 5 FM + 4 PSG + PCM nos canais escolhidos em `--pcm-channels`
  (padrão 1,2; os outros ficam para o jogo). Amostras da música usam os ids 1–63 da tabela do
  XGM; os efeitos do jogo devem usar ids ≥ 64. Cada arquivo é decodificado de volta e comparado
  quadro a quadro com o fluxo da prévia.
* **VGM 1.71** (para players/emuladores): o PCM dos canais da música vai pré-misturado num
  data block. Não é a entrada indicada para o rescomp (viraria uma amostra única gigante).
* Loop: `loopStart`/`loopEnd` do MIDI → `$7E` no XGM e offset de loop no VGM.

## Fase 7 — Interface gráfica

```
pip install numpy scipy PySide6
python run_gui.py            (ou: python -m mdconv.gui)
```

Fluxo na janela:

1. **Arquivos**: MIDI, SF2, a pasta da biblioteca de patches (compartilhada entre as músicas)
   e a pasta de saída → **Abrir música**.
2. **Instrumentos**: cada instrumento com canais, função no arranjo, destino (FM/PSG/PCM) e
   status do patch. Para os que estão *provisório*: **Gerar candidatos**, ouvir cada A/B
   (original e FM, nota por nota) e **Aprovar escolhido**. Um patch aprovado vale para
   todas as músicas que usam o mesmo instrumento (mesmo banco/programa e nome do preset).
3. **Arranjo**: canais PCM da música (os desmarcados ficam para os efeitos do jogo), vibrato,
   equilíbrio bateria × resto; o relatório mostra cada decisão.
4. **Loop**: automático (marcador do MIDI; sem marcador, estima pela forma como a música
   termina), só marcador, ou sem loop. **Ouvir a emenda** toca o fim e a volta; se a volta
   estiver errada, escolha o compasso de início.
5. **Prévia e exportação**: prévia WAV, exportação XGM v1 / VGM validada.

Projetos podem ser salvos (`.mdcproj`) pelo menu Arquivo. Tarefas longas rodam em segundo
plano e podem ser canceladas.

Toda a lógica da janela está em `mdconv/app/project.py` (testada sem Qt, `tests/unit/test_phase7.py`).

## Perfis de jogos de Mega Drive

A pasta `perfis_megadrive/` do projeto guarda um `.json` por jogo (onde fica o driver de som e quais
números são músicas) e vai junto com o conversor em todos os executáveis. Para contribuir com um
jogo novo: rode a análise automática na aba Extração, confira as prévias e copie o `.json` gerado
para essa pasta (veja `perfis_megadrive/LEIA-ME.md`). Perfis na sua pasta de usuário têm prioridade
sobre os do projeto.

A aba **Catálogo Mega Drive** lista os jogos e deixa cada pessoa marcar se funcionou (verde),
não funcionou (vermelho) ou ainda não foi testado (cinza), vincular a ROM aberta ao jogo certo e
**exportar um CSV** com o resultado — é a planilha que diz quais jogos precisam de atenção.

## Compilar (Windows e Linux)

Passo a passo em [COMPILAR.md](COMPILAR.md). Resumo, na pasta do projeto:

- **Windows:** `packaging\build_windows.bat` (só precisa do Python 3.12 de 64 bits; as DLLs dos
  emuladores vão prontas).
- **Linux:** `sh packaging/build_appimage.sh` (AppImage), `sh packaging/build_linux.sh` (pasta/.tar.gz)
  ou, no Arch, `cd packaging/arch && makepkg -si`. Precisa de python e gcc.
- **GitHub Actions:** gera os dois de uma vez.

## Emuladores usados na prévia

* **FM (YM2612): Nuked-OPN2** no modo YM2612 — emulação ciclo a ciclo feita a partir do
  chip, por Alexey Khokholov (Nuke.YKT), licença **GNU LGPL 2.1 ou posterior**. Fica em
  `mdconv/chips/native/nuked_opn2/` (fonte original sem alterações + `LICENSE`) e é
  compilado como biblioteca separada (`nuked_opn2.dll`), como a LGPL pede. A emulação do
  DAC do YM2612 (efeito ladder) é marcada pelo autor como não verificada.
* **Busca de patches**: emulador próprio (`mdchips.c`), ~4× mais rápido, com o mesmo nível
  do Nuked (±0,3 dB na música inteira do song001).
* **PSG e PCM**: emulador próprio e mixer que imita o do XGM.
* **68000 (extração de jogos do Mega Drive): Musashi**, de Karl Stenerud, licença **MIT**
  (`mdconv/chips/native/md68k/musashi/LICENSE.txt`), configurado só para o 68000 e compilado em
  `md68k.dll` junto com `mdsnd.c` (mapa de memória do Mega Drive) e `z80.c` (Z80), ambos deste projeto.

Os arquivos exportados (XGM/VGM) não dependem do emulador: são as mesmas escritas de
registrador.

O núcleo YM2612 do Genesis Plus GX **não** faz parte do projeto nem do executável: a
licença dele não permite redistribuição comercial. Quem o tiver pode compilá-lo localmente
(`tools/gpgx_reference/`), apontar `MDCONV_GPGX_LIB` e usar `--engine gpgx` para comparar.

## Banco de patches prontos (.tfi)

```
python -m mdconv.cli import-tfi pasta_com_tfi --bank banco_tfi
python -m mdconv.cli fm-fit musica.mid banco.sf2 saida --bank banco_tfi [--bank-only]
```

`import-tfi` lê todos os `.tfi` (subpastas incluídas), descarta repetidos e grava um banco
único (`banco.npz` + `banco.json`) com os parâmetros e um descritor do som de cada patch.
No ajuste, os patches do banco mais parecidos com o instrumento do SF2 (testando oitavas)
passam pela mesma avaliação da busca do zero; os melhores ganham um refinamento local.
Entram como candidatos extras, marcados com o arquivo de origem; a escolha continua sendo
pelo ouvido. Na interface: campo "Banco de patches prontos" e as opções do ajuste.

Formato TFI: 42 bytes; operadores na ordem dos **registradores** (S1, S3, S2, S4) —
conferido em 30 mil patches. SSG-EG ainda não é reproduzido (o patch entra sem ele).

## Exportação para o Furnace (.fur)

`--format fur` (ou a caixa "Furnace 0.6.8.3 (.fur)" na interface) grava um projeto do
Furnace 0.6.8.3 com a MESMA distribuição do XGM: cada nota no canal em que o driver a pôs.

* Sistema "YM2612 DualPCM" (FM 1–5 + PCM 1–2) + "SN76489"; sem PCM, "YM2612" (6 FM).
  YM2612 no modo do Mega Drive 1 (com ladder).
* Instrumentos: os patches FM aprovados; para o PSG, instrumentos com macro de volume
  ADSR no lugar do envelope do driver; a bateria vira um instrumento de amostras com
  mapa de notas (cada tecla do kit toca a sua amostra).
* Tempo exato: 60 ticks/s com "virtual tempo" BPM/150 (o mesmo acumulador do driver do
  GBA). Uma linha = 1 tick do MIDI até 150 BPM, 2 acima disso; notas entre linhas
  recebem EDxx. Mudanças de andamento: FDxx.
* Pitch bend → E5xx; vibrato do m4a → 04xy + E4xx com forma triangular (E303); volume →
  coluna de volume (passos de TL iguais aos do driver); pan → 08xy; loop → 0Bxx.
* No PSG as notas ficam 2 oitavas abaixo do que soam: é a convenção do Furnace para o
  SN76489 (o "A-4" do PSG toca 1760 Hz).

Conferido no próprio Furnace 0.6.8.3 (compilado em linha de comando): o arquivo abre,
toca com a duração, o loop e o equilíbrio FM/PSG/PCM da prévia, e as alturas batem.
Os testes `tests/unit/test_furnace.py` usam o Furnace se `MDCONV_FURNACE` apontar para ele.

## Canais FM fixos e andamento na grade de 60 Hz

Na aba de arranjo:

* **Canais FM** — *Fixos por parte*: como um músico faria no tracker, melodia, baixo e as partes
  que mais tocam ganham canal FM próprio (o patch não é recarregado e o release de uma nota
  termina sob a seguinte); as vozes que não cabem vão para o PSG e ecos/partes curtas dividem
  um canal. *Nota a nota*: o comportamento anterior (qualquer canal livre). *Automático*
  (padrão): converte dos dois jeitos e fica com o que perde menos notas e recarrega menos
  patches — o relatório mostra a comparação. Na song003: cargas de patch 221 → 143, notas
  encurtadas 24 → 7, omitidas 17 → 10. Na song001, mais densa, o automático mantém o nota a nota.
* **Travar o andamento na grade de 60 Hz** — ajusta o andamento (até 6%) para o passo rítmico
  durar um número inteiro de quadros: na song003, 102 → 100 BPM e a semicolcheia em 6 quadros
  exatos em vez de alternar 6 e 5 (o mesmo andamento que o músico usou na versão de tracker).
* Em qualquer modo, escritas FM que repetem o valor que o registrador já tem são omitidas
  (como o Furnace faz): na song003, 4.279 escritas a menos e música 35% menor no XGM, sem
  mudança no som.

## Chimbais e pratos no ruído do PSG (opcional)

Na aba de arranjo, "Chimbais e pratos no ruído do PSG" (na linha de comando, `--noise-metals`)
manda as peças metálicas do kit para o canal de ruído do PSG em vez do PCM:

* **Quais peças:** decididas pelo som, não pela tecla (os kits do GBA não seguem o mapa GM):
  agudas (centroide ≥ 3,5 kHz, boa parte da energia acima de 5 kHz) e sem corpo grave. Bumbo,
  caixa e tons ficam no PCM. O relatório lista as peças escolhidas.
* **Como soam:** ruído branco N/512, com o envelope de volume da própria amostra quadro a
  quadro (passos de 2 dB) e o volume de cada nota (velocity) — coisa que o PCM do XGM não
  tem. Um golpe novo corta o anterior; no mesmo quadro vence a peça mais forte.
* **Ganho:** menos golpes de PCM disputando os canais da música (na song001, as caudas
  cortadas caíram de 239 para 14) e menos ROM gasta com amostras (65,5 → 30,5 KB).
* **Exportação:** XGM, VGM e Furnace (canal "Ruído", com macros de volume e de modo de ruído).
* **Referência:** jogos comerciais fazem o mesmo (ex.: Eternal Champions, ruído branco N/1024
  com decaimento de 2 dB por quadro).

## Publicar o executável no GitHub

1. Gere com `packaging\build_windows.bat` (e, para Linux, `sh packaging/build_appimage.sh`). No fim
   eles criam `dist\MegaDriveAudioConverter-windows-x64.zip` e o `.AppImage`.
2. No GitHub: **Releases → Draft a new release**, escolha uma tag (ex.: `v0.1`) e
   arraste o **.zip** (e o `.AppImage`, se tiver) para "Attach binaries". Publique.
3. Quem baixar: extrai o zip e roda `MegaDriveAudioConverter.exe` **de dentro da pasta
   extraída** (ao lado da pasta `_internal`).

Não publique só o `.exe` da pasta `dist\MegaDriveAudioConverter`: ele depende da pasta
`_internal` (Python, Qt, emuladores) e, sozinho, pede `python3xx.dll`. Também não vale
mandar o executável pelo próprio repositório (git) — use os Releases.

Requisitos de quem usa: Windows 10 ou 11, 64 bits (o Python 3.14 não roda em Windows 7/8).
