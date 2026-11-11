# Mega Drive Audio Converter

Conversor de músicas de GBA (MIDI + SoundFont extraídos pelo GBA Mus Ripper) para o
som do Mega Drive (YM2612 + SN76489), com exportação VGM e, depois, XGM para SGDK.

Estado atual: **Fase 3 — motor FM e patches YM2612** (Fases 1 e 2 aprovadas).

## Requisitos

- Python 3.10 ou mais novo
- numpy
- pytest (para os testes)

- scipy (reamostragem e otimização)
- gcc (MinGW-w64 no Windows) para compilar o emulador do chip na primeira execução
  (o executável da Fase 9 virá com a DLL pronta)

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

## Executável para Windows

Na pasta do projeto, com Python 3.10+ instalado:

```
packaging\build_windows.bat
```

O script instala numpy, scipy, PySide6 e PyInstaller, roda os testes e gera
`dist\MegaDriveAudioConverter\MegaDriveAudioConverter.exe`. O emulador nativo vai pronto
em `mdconv/chips/native/mdchips.dll` (gerada por `tools/build_dll.sh`; não precisa de
compilador no Windows).

## Emuladores usados na prévia

* **FM (YM2612): Nuked-OPN2** no modo YM2612 — emulação ciclo a ciclo feita a partir do
  chip, por Alexey Khokholov (Nuke.YKT), licença **GNU LGPL 2.1 ou posterior**. Fica em
  `mdconv/chips/native/nuked_opn2/` (fonte original sem alterações + `LICENSE`) e é
  compilado como biblioteca separada (`nuked_opn2.dll`), como a LGPL pede. A emulação do
  DAC do YM2612 (efeito ladder) é marcada pelo autor como não verificada.
* **Busca de patches**: emulador próprio (`mdchips.c`), ~4× mais rápido, com o mesmo nível
  do Nuked (±0,3 dB na música inteira do song001).
* **PSG e PCM**: emulador próprio e mixer que imita o do XGM.

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
