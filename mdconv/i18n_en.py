"""Tradução da interface para o inglês (chave = texto em português do código).

Gerado a partir dos textos com tr(); para acrescentar, inclua a frase em português exatamente
como está no código.
"""

EN = {
    "Instrumento":
        "Instrument",
    "Canais":
        "Channels",
    "Notas":
        "Notes",
    "Função":
        "Role",
    "Destino":
        "Destination",
    "Canal no chip":
        "Chip channel",
    "Patch FM":
        "FM patch",
    "1. Instrumentos":
        "1. Instruments",
    "2. Arranjo":
        "2. Arrangement",
    "4. Prévia e exportação":
        "4. Preview and export",
    "Extração":
        "Extraction",
    "Conversão":
        "Conversion",
    "&Arquivo":
        "&File",
    "Atualizar patches travados pela biblioteca…":
        "Update locked patches from the library…",
    "Destravar patches desta música…":
        "Unlock this song's patches…",
    "Histórico de decisões…":
        "Decision history…",
    "Sair":
        "Exit",
    "&Ferramentas":
        "&Tools",
    "Conversores e efeitos (VGM, efeitos de chip, WAV, banco .tfi)…":
        "Converters and effects (VGM, chip effects, WAV, .tfi bank)…",
    "A&juda":
        "&Help",
    "Sobre":
        "About",
    "Arquivos":
        "Files",
    "Pasta criada por 'import-tfi' (patches .tfi de jogos e packs). Opcional: dá candidatos extras a partir de patches prontos.":
        "Folder created by 'import-tfi' (.tfi patches from games and packs). Optional: gives extra candidates from ready-made patches.",
    "vazio = banco General MIDI padrão (para MIDIs sem SoundFont)":
        "empty = default General MIDI bank (for MIDIs without a SoundFont)",
    "O SoundFont do jogo (aba Extração ou GBA Mus Ripper). Para um MIDI comum, sem SoundFont, deixe vazio: os instrumentos e a bateria vêm do banco General MIDI padrão do programa.":
        "The game's SoundFont (Extraction tab or GBA Mus Ripper). For a regular MIDI without a SoundFont, leave it empty: instruments and drums come from the program's default General MIDI bank.",
    "Patches aprovados ficam aqui e servem para todas as músicas que usam o mesmo instrumento do SF2.":
        "Approved patches are stored here and apply to every song that uses the same SF2 instrument.",
    "Abrir música":
        "Open song",
    "Selecione um instrumento.":
        "Select an instrument.",
    "Gerar candidatos FM":
        "Generate FM candidates",
    "Feedback 6–7 costuma gerar chiado no fim das notas.":
        "Feedback 6–7 often causes hiss at the end of notes.",
    "Liberar detune (coro — bom para strings/ensembles)":
        "Allow detune (chorus — good for strings/ensembles)",
    "Incluir candidatos do banco de patches prontos":
        "Include candidates from the ready-made patch bank",
    "Só do banco (bem mais rápido)":
        "Bank only (much faster)",
    "Incluir patches parecidos que já aprovei (biblioteca)":
        "Include similar patches I already approved (library)",
    "Compara o som com os patches aprovados em outras músicas e jogos e oferece os mais parecidos (como estão e refinados). Quanto mais você aprova, melhores ficam as sugestões.":
        "Compares the sound with patches approved in other songs and games and offers the closest ones (as they are and refined). The more you approve, the better the suggestions get.",
    "Gerar candidatos":
        "Generate candidates",
    "⚡ Rascunho automático (todos os instrumentos)":
        "⚡ Automatic draft (all instruments)",
    "Para cada instrumento FM ainda sem patch aprovado: gera os candidatos e aprova o de menor custo, marcado como rascunho. Depois é só ouvir e trocar os que não ficaram bons.":
        "For each FM instrument still without an approved patch: generates the candidates and approves the lowest-cost one, marked as a draft. Then just listen and replace the ones that didn't turn out well.",
    "Busca:":
        "Search:",
    "Feedback máximo:":
        "Max feedback:",
    "▶ Ouvir A/B":
        "▶ Listen A/B",
    "✔ Aprovar escolhido":
        "✔ Approve selected",
    "✖ Recusar":
        "✖ Reject",
    "Ouviu e não gostou: fica registrado no histórico de decisões (usado para melhorar as sugestões) e o rascunho automático o ignora.":
        "Listened and didn't like it: it is recorded in the decision history (used to improve suggestions) and the automatic draft ignores it.",
    "Opções":
        "Options",
    "Bateria em PCM (o canal FM 6 vira DAC)":
        "Drums as PCM (FM channel 6 becomes the DAC)",
    "Vibrato (modulação do m4a)":
        "Vibrato (m4a modulation)",
    "Travar o andamento na grade de 60 Hz (ritmo sem oscilação; muda o andamento em até 6%)":
        "Lock the tempo to the 60 Hz grid (steady rhythm; changes the tempo by up to 6%)",
    "Ajusta o andamento para o passo rítmico durar um número inteiro de quadros, como um\nmúsico faria no tracker. Ex.: 102 → 100 BPM deixa a semicolcheia em 6 quadros exatos\nem vez de alternar 6 e 5.":
        "Adjusts the tempo so the rhythmic step lasts a whole number of frames, as a\nmusician would do in a tracker. E.g.: 102 → 100 BPM makes the sixteenth note exactly 6 frames\ninstead of alternating 6 and 5.",
    "Começar na primeira nota (tira o silêncio do início da música)":
        "Start at the first note (removes the silence at the start of the song)",
    "Os MIDIs extraídos costumam começar com um trecho mudo (o GBA Mus Ripper põe um\ncompasso inteiro). Marcado, a música começa direto na primeira nota; o loop acompanha.":
        "Extracted MIDIs often start with a silent stretch (GBA Mus Ripper adds a whole\nbar). When checked, the song starts right at the first note; the loop follows.",
    "Chimbais e pratos no ruído do PSG (libera o PCM para bumbo e caixa)":
        "Hi-hats and cymbals on the PSG noise (frees the PCM for kick and snare)",
    "Peças metálicas do kit (detectadas pelo som: agudas e sem grave) tocam no canal de\nruído do PSG, com o envelope da própria amostra e o volume de cada nota.\nOs canais PCM ficam para bumbo, caixa e tons, e a ROM gasta menos com amostras.":
        "Metallic kit pieces (detected by sound: high-pitched with no low end) play on the PSG\nnoise channel, with the sample's own envelope and each note's volume.\nThe PCM channels are left for kick, snare and toms, and the ROM spends less on samples.",
    "Automático":
        "Automatic",
    "Canais PCM da música:":
        "Song PCM channels:",
    "Canais PSG de tom:":
        "PSG tone channels:",
    "Canais FM:":
        "FM channels:",
    "Equilíbrio bateria × resto:":
        "Drums × rest balance:",
    "Aplicar e refazer o arranjo":
        "Apply and redo the arrangement",
    "Modo:":
        "Mode:",
    "Loop atual:":
        "Current loop:",
    "Usar este compasso":
        "Use this bar",
    "Voltar à detecção":
        "Back to detection",
    "Início do loop no compasso:":
        "Loop start at bar:",
    "▶ Ouvir a emenda (fim da música → volta do loop)":
        "▶ Listen to the seam (end of song → loop return)",
    "Sem marcador no MIDI, a volta é estimada pelo jeito que a música termina. Ouça a emenda: se a música voltar no lugar errado, escolha o compasso certo.":
        "Without a marker in the MIDI, the loop return is estimated from how the song ends. Listen to the seam: if the song loops back to the wrong place, choose the right bar.",
    "Gerar prévia":
        "Generate preview",
    "▶ Tocar":
        "▶ Play",
    "■ Parar":
        "■ Stop",
    "Nuked-OPN2 (modo YM2612, mais fiel)":
        "Nuked-OPN2 (YM2612 mode, more accurate)",
    "Emulador próprio (mais rápido)":
        "Built-in emulator (faster)",
    "Só muda a prévia; os arquivos XGM/VGM são os mesmos.":
        "Only changes the preview; the XGM/VGM files are the same.",
    "Exportação":
        "Export",
    "VGM 1.71 (players/emuladores)":
        "VGM 1.71 (players/emulators)",
    "Projeto do Furnace com as mesmas notas, instrumentos e bateria, para editar.":
        "Furnace project with the same notes, instruments and drums, for editing.",
    "padrão: nome do MIDI":
        "default: MIDI name",
    "Formatos:":
        "Formats:",
    "Nome dos arquivos:":
        "File name:",
    "Título (VGM):":
        "Title (VGM):",
    "Jogo (VGM):":
        "Game (VGM):",
    "Exportar":
        "Export",
    "Abrir pasta de saída":
        "Open output folder",
    "Cancelar":
        "Cancel",
    "Abrir projeto":
        "Open project",
    "Salvar projeto":
        "Save project",
    "🔓 Patches destravados: a música segue a biblioteca até o próximo salvamento.":
        "🔓 Patches unlocked: the song follows the library until the next save.",
    "Aparece depois de converter (aba Arranjo → \"Aplicar e refazer o arranjo\" ou prévia).\nInformativo: o driver escolhe o canal nota a nota.":
        "Shows up after converting (Arrangement tab → \"Apply and redo the arrangement\" or preview).\nFor information: the driver picks the channel note by note.",
    "Canais do chip usados na última conversão (o driver escolhe nota a nota):\n":
        "Chip channels used in the last conversion (the driver picks note by note):\n",
    "Rascunho automático":
        "Automatic draft",
    "✔ Pronto. Ouça os candidatos e aprove o que soar melhor.":
        "✔ Done. Listen to the candidates and approve the one that sounds best.",
    "Cada aprovação e recusa fica registrada com o tipo de instrumento, as opções da busca e os custos medidos. Com algumas centenas de decisões, esses dados servem para ajustar as sugestões ao seu ouvido.":
        "Every approval and rejection is recorded with the instrument type, the search options and the measured costs. With a few hundred decisions, this data helps tune the suggestions to your ear.",
    "Aprovando":
        "Approving",
    "Arranjo":
        "Arrangement",
    "Emenda do loop":
        "Loop seam",
    "Prévia da música":
        "Song preview",
    "Montando o banco de patches .tfi (milhares de patches levam alguns minutos)":
        "Building the .tfi patch bank (thousands of patches take a few minutes)",
    "Novo projeto":
        "New project",
    "Abrir projeto…":
        "Open project…",
    "Salvar projeto como…":
        "Save project as…",
    "Biblioteca de patches:":
        "Patch library:",
    "Banco de patches prontos:":
        "Ready-made patch bank:",
    "Pasta de saída:":
        "Output folder:",
    "Procurar…":
        "Browse…",
    "Rápido (~30 s a 2 min)":
        "Fast (~30 s to 2 min)",
    "Completo (lento)":
        "Full (slow)",
    "Candidatos (em cada WAV: original, depois FM, nota por nota):":
        "Candidates (in each WAV: original, then FM, note by note):",
    "  (os canais não marcados ficam livres para os efeitos do jogo)":
        "  (unchecked channels are left free for the game's sound effects)",
    "Desmarque para a música não usar este tom do PSG. As notas que iriam para ele\nficam no FM (ou vão para outro PSG marcado); o que não couber aparece no relatório.":
        "Uncheck so the song doesn't use this PSG tone. Notes that would go to it\nstay on FM (or go to another checked PSG); whatever doesn't fit shows up in the report.",
    "  (desmarque se o PSG chiar; os desmarcados ficam mudos ou livres para o jogo)":
        "  (uncheck if the PSG buzzes; unchecked ones stay silent or free for the game)",
    "Automático (o melhor dos dois)":
        "Automatic (the better of the two)",
    "Fixos por parte (como no tracker)":
        "Fixed per part (like in a tracker)",
    "Nota a nota (dinâmico)":
        "Note by note (dynamic)",
    "  atenuação FM/PSG:":
        "  FM/PSG attenuation:",
    "Automático (marcadores; senão estima)":
        "Automatic (markers; otherwise estimates)",
    "Só com marcador loopStart/loopEnd":
        "Only with loopStart/loopEnd marker",
    "Sem loop (toca uma vez)":
        "No loop (plays once)",
    "   Emulador do FM:":
        "   FM emulator:",
    "Aguarde a tarefa atual terminar (ou cancele).":
        "Wait for the current task to finish (or cancel it).",
    "■ Cancelado.":
        "■ Cancelled.",
    "Cancelando no próximo passo…":
        "Cancelling at the next step…",
    "Escolha a pasta":
        "Choose the folder",
    "Escolha o arquivo":
        "Choose the file",
    "Espere a tarefa em andamento terminar.":
        "Wait for the running task to finish.",
    "A biblioteca tem patches diferentes dos travados neste projeto para {0} instrumento(s):\n\n{1}\n\nAtualizar esta música para os patches da biblioteca?\n(Não = manter os travados: a música continua soando como foi salva.)":
        "The library has patches that differ from the ones locked in this project for {0} instrument(s):\n\n{1}\n\nUpdate this song to the library patches?\n(No = keep the locked ones: the song keeps sounding as it was saved.)",
    "🔒 Mantidos os patches travados do projeto.":
        "🔒 Kept the project's locked patches.",
    ". Salve o projeto para manter.":
        ". Save the project to keep it.",
    "Abra uma música primeiro.":
        "Open a song first.",
    "Esta música ainda não tem patches travados (eles são travados ao salvar o projeto).":
        "This song has no locked patches yet (they are locked when the project is saved).",
    "Esta música não tem patches travados.":
        "This song has no locked patches.",
    "Destravar todos os patches desta música? Ela passa a usar os patches atuais da biblioteca (até salvar o projeto de novo, que trava outra vez).":
        "Unlock all patches of this song? It will use the library's current patches (until the project is saved again, which locks them again).",
    "{0} — canais {1} — {2}":
        "{0} — channels {1} — {2}",
    "), não precisa de patch.":
        "), it doesn't need a patch.",
    "Todos os instrumentos FM já têm patch aprovado.":
        "All FM instruments already have an approved patch.",
    "O patch foi gravado, mas o instrumento continua sem patch aprovado na tabela. Confira se a pasta da biblioteca é a mesma usada na conversão e me mande o texto do registro abaixo.":
        "The patch was saved, but the instrument still has no approved patch in the table. Check that the library folder is the same one used in the conversion and send me the log text below.",
    "Selecione o instrumento e o candidato.":
        "Select the instrument and the candidate.",
    "Este candidato está aprovado. Aprove outro antes de recusar este.":
        "This candidate is approved. Approve another one before rejecting this one.",
    "Por origem do candidato (aprovados / recusados):":
        "By candidate origin (approved / rejected):",
    "Selecione o instrumento na tabela.":
        "Select the instrument in the table.",
    "Selecione um candidato na lista (gere os candidatos antes, se a lista estiver vazia).":
        "Select a candidate in the list (generate the candidates first if the list is empty).",
    "Refazendo o arranjo…":
        "Redoing the arrangement…",
    "sem loop — a música toca uma vez e para.":
        "no loop — the song plays once and stops.",
    "de {0:.2f} s (compasso {1:.0f}) até {2:.2f} s — origem: {3}. {4}":
        "from {0:.2f} s (bar {1:.0f}) to {2:.2f} s — source: {3}. {4}",
    "⚠ ESTIMADO — ":
        "⚠ ESTIMATED — ",
    "marque ao menos um formato (XGM, VGM ou Furnace)":
        "check at least one format (XGM, VGM or Furnace)",
    "Cancelado.":
        "Cancelled.",
    "Projeto ({0})":
        "Project ({0})",
    "Projeto salvo: {0} ({1} patch(es) FM travado(s))":
        "Project saved: {0} ({1} FM patch(es) locked)",
    "Abrindo {0}":
        "Opening {0}",
    "Os patches travados já são iguais aos da biblioteca.":
        "The locked patches are already the same as the library's.",
    "🔒 Travas atualizadas pela biblioteca: ":
        "🔒 Locks updated from the library: ",
    "{0}: {1} nota(s)":
        "{0}: {1} note(s)",
    "\nNo chip: {0}":
        "\nOn the chip: {0}",
    "\nEste instrumento não toca no FM (":
        "\nThis instrument doesn't play on FM (",
    "Gerando candidatos FM para {0}":
        "Generating FM candidates for {0}",
    "✔ Aprovado: {0} candidato #{1} → {2}":
        "✔ Approved: {0} candidate #{1} → {2}",
    "✖ {0}: candidato #{1} recusado ({2}).":
        "✖ {0}: candidate #{1} rejected ({2}).",
    "Arquivo: {0}":
        "File: {0}",
    "Aprovados: {0} · Recusados: {1} · Rascunhos automáticos: {2} · Audições: {3}":
        "Approved: {0} · Rejected: {1} · Automatic drafts: {2} · Listens: {3}",
    "✔ Exportado em ":
        "✔ Exported to ",
    "Banco pronto: {0} patches diferentes de {1} arquivos ({2} com defeito; {3} usam SSG-EG, importados sem ele). Ordem dos operadores conferida em {4} patches de algoritmo 4.":
        "Bank ready: {0} distinct patches from {1} files ({2} broken; {3} use SSG-EG, imported without it). Operator order checked in {4} algorithm-4 patches.",
    "\n\nO campo 'Banco de patches prontos' já aponta para ele.":
        "\n\nThe 'Ready-made patch bank' field already points to it.",
    "Convertendo {0} VGM":
        "Converting {0} VGM",
    "Convertendo {0} efeito(s) de chip":
        "Converting {0} chip effect(s)",
    "Preparando {0} efeito(s) sonoro(s)":
        "Preparing {0} sound effect(s)",
    "<b>{0}</b> — versão {1}<br><br>Converte músicas (MIDI + SF2) para o som do Mega Drive: YM2612 (FM), SN76489 (PSG) e PCM, exportando XGM v1 (SGDK), VGM e Furnace.<br><br><b>Origens suportadas:</b><br>• <b>GBA</b> — MIDI + SF2 da aba Extração (motor de som da Nintendo) ou do GBA Mus Ripper<br>• Arcades Capcom <b>CPS1</b> — MIDI + SF2 da aba Extração, com os timbres FM originais prontos como biblioteca de patches.<br>• <b>MIDI comum</b> (General MIDI), com ou sem SoundFont — sem SF2, usa o banco General MIDI padrão do programa.<br>• VGM de <b>AY-3-8910</b> (Moon Patrol, MSX, Spectrum…) e <b>YM2203</b> (Ghosts 'n Goblins…) — conversão direta chip a chip (menu Ferramentas).<br><br>Aba <b>Extração</b>: extrator de áudio {2} (parte CPS1 portada do VGMTrans, licença zlib).<br><br>Software livre.<br><br>Prévia do FM com o <b>Nuked-OPN2</b> © Alexey Khokholov (Nuke.YKT), licença GNU LGPL 2.1 ou posterior — código-fonte e licença na pasta <code>mdconv/chips/native/nuked_opn2</code>.":
        "<b>{0}</b> — version {1}<br><br>Converts songs (MIDI + SF2) to the Mega Drive sound: YM2612 (FM), SN76489 (PSG) and PCM, exporting XGM v1 (SGDK), VGM and Furnace.<br><br><b>Supported sources:</b><br>• <b>GBA</b> — MIDI + SF2 from the Extraction tab (Nintendo's sound engine) or from GBA Mus Ripper<br>• Capcom <b>CPS1</b> arcades — MIDI + SF2 from the Extraction tab, with the original FM voices ready as a patch library.<br>• <b>Regular MIDI</b> (General MIDI), with or without a SoundFont — without an SF2, it uses the program's default General MIDI bank.<br>• <b>AY-3-8910</b> VGM (Moon Patrol, MSX, Spectrum…) and <b>YM2203</b> (Ghosts 'n Goblins…) — direct chip-to-chip conversion (Tools menu).<br><br><b>Extraction</b> tab: audio extractor {2} (CPS1 part ported from VGMTrans, zlib license).<br><br>Free software.<br><br>FM preview with <b>Nuked-OPN2</b> © Alexey Khokholov (Nuke.YKT), GNU LGPL 2.1 or later license — source code and license in the <code>mdconv/chips/native/nuked_opn2</code> folder.",
    "Há uma tarefa em andamento. Sair mesmo assim?":
        "A task is running. Exit anyway?",
    "Antes de abrir: ":
        "Before opening: ",
    "Silêncio do início removido: {0:.2f} s (a música começa na primeira nota).":
        "Silence at the start removed: {0:.2f} s (the song starts at the first note).",
    "✔ {0} instrumentos; {1} ainda sem patch FM aprovado":
        "✔ {0} instruments; {1} still without an approved FM patch",
    "• {0}  (travado: {1} · biblioteca: {2})":
        "• {0}  (locked: {1} · library: {2})",
    "✔ APROVADO  ":
        "✔ APPROVED  ",
    "✔ {0} instrumento(s) com patch de rascunho. Ouça a prévia da música e aprove outro candidato onde precisar.":
        "✔ {0} instrument(s) with a draft patch. Listen to the song preview and approve another candidate where needed.",
    "Aprovando #{0} de {1}…":
        "Approving #{0} of {1}…",
    "Equilíbrio automático: FM/PSG −{0} dB.":
        "Automatic balance: FM/PSG −{0} dB.",
    "✔ Prévia: {0}":
        "✔ Preview: {0}",
    "Arquivo não encontrado: {0}":
        "File not found: {0}",
    "✔ Efeitos prontos em {0}: .vgm, .c (SGDK) e prévia de cada um":
        "✔ Effects ready in {0}: .vgm, .c (SGDK) and a preview of each",
    "Pronto: {0} arquivo(s) em\n{1}\n\nNo jogo: chame nome_play() para disparar e nome_update() uma vez por quadro.":
        "Done: {0} file(s) in\n{1}\n\nIn the game: call name_play() to trigger and name_update() once per frame.",
    "✔ {0} efeito(s) prontos em {1} ({2:.0f} KB na ROM). No .res: WAV nome \"arquivo.wav\" XGM":
        "✔ {0} effect(s) ready in {1} ({2:.0f} KB in ROM). In the .res: WAV name \"file.wav\" XGM",
    "Pronto: {0} arquivo(s) em\n{1}\n\n{2:.0f} KB na ROM. Prévias para ouvir no PC na subpasta 'previa'.":
        "Done: {0} file(s) in\n{1}\n\n{2:.0f} KB in ROM. Previews to listen to on the PC in the 'previa' subfolder.",
    "Não consegui ler o projeto: {0}":
        "Couldn't read the project: {0}",
    "; {0} com patch travado no projeto.":
        "; {0} with a patch locked in the project.",
    "⚠ não consegui comparar com a biblioteca: {0}":
        "⚠ couldn't compare with the library: {0}",
    "✖ RECUSADO  ":
        "✖ REJECTED  ",
    "\n\nBusca: {0}. Pode levar alguns minutos por instrumento.\nOs patches entram como RASCUNHO: ouça a música e troque os que não ficarem bons.":
        "\n\nSearch: {0}. It may take a few minutes per instrument.\nThe patches go in as a DRAFT: listen to the song and replace the ones that don't sound good.",
    "⚡ rascunho: {0}":
        "⚡ draft: {0}",
    "Silêncio do início removido: {0:.2f} s.":
        "Silence at the start removed: {0:.2f} s.",
    "✔ {0} música(s) convertida(s) em {1}":
        "✔ {0} song(s) converted in {1}",
    "Pronto: {0} arquivo(s) em\n{1}":
        "Done: {0} file(s) in\n{1}",
    "⚠ não consegui travar os patches: {0}":
        "⚠ couldn't lock the patches: {0}",
    "🔒 {0} patch(es) travado(s) no projeto: ":
        "🔒 {0} patch(es) locked in the project: ",
    " (rascunho)":
        " (draft)",
    "; {0} com erro (ver registro)":
        "; {0} with errors (see the log)",
    "\n\n{0} arquivo(s) com erro: veja o registro.":
        "\n\n{0} file(s) with errors: see the log.",
    "✔ {0}: {1} quadros, {2} escritas":
        "✔ {0}: {1} frames, {2} writes",
    "Gerar candidatos e aprovar automaticamente o melhor para {0} instrumento(s)?\n\n":
        "Generate candidates and automatically approve the best one for {0} instrument(s)?\n\n",
    "1. ROM do jogo — .zip do MAME ou pasta (CPS1), .gba (Game Boy Advance), .md/.bin (Mega Drive)":
        "1. Game ROM — MAME .zip or folder (CPS1), .gba (Game Boy Advance), .md/.bin (Mega Drive)",
    "Escolha o arquivo da ROM…":
        "Choose the ROM file…",
    "Só para CPS1: o jogo é reconhecido pelo nome do zip; escolha aqui se não for.":
        "CPS1 only: the game is recognized by the zip name; choose it here if it isn't.",
    "Carregar":
        "Load",
    "2. Músicas":
        "2. Songs",
    "3. O que gerar":
        "3. What to generate",
    "MIDI das músicas":
        "Song MIDIs",
    "Samples do Oki (.wav) — CPS1":
        "Oki samples (.wav) — CPS1",
    "Biblioteca de patches FM — CPS1":
        "FM patch library — CPS1",
    "Completa (3 amostras/instr.)":
        "Full (3 samples/instr.)",
    "Rápida (1 amostra/instr.)":
        "Fast (1 sample/instr.)",
    "Canais dos efeitos…":
        "Effect channels…",
    "Escolhe em quais canais do PSG e do FM cada efeito de chip toca, para efeitos que soam juntos no jogo não brigarem pelo mesmo canal.":
        "Chooses which PSG and FM channels each chip effect plays on, so effects that sound together in the game don't fight over the same channel.",
    "Opções do Neo Geo…":
        "Neo Geo options…",
    "Canais PCM da música, peças metálicas no ruído do PSG e tamanho das amostras (o que mais pesa na ROM).":
        "Song PCM channels, metallic pieces on the PSG noise and sample size (what weighs most in the ROM).",
    "4. Pasta de saída":
        "4. Output folder",
    "Extrair":
        "Extract",
    "Abrir pasta":
        "Open folder",
    "O registro da extração aparece aqui.":
        "The extraction log shows up here.",
    "5. Levar para a conversão":
        "5. Take to conversion",
    "Extraia um jogo para ver os MIDIs aqui.":
        "Extract a game to see its MIDIs here.",
    "Abrir na conversão →":
        "Open in conversion →",
    "Registro":
        "Log",
    "Escolha a ROM":
        "Choose the ROM",
    "ROMs (*.zip *.gba *.agb *.md *.bin *.gen);;Romset do MAME (CPS1) (*.zip);;Game Boy Advance (*.gba *.agb);;Mega Drive (*.md *.bin *.gen);;Todos (*.*)":
        "ROMs (*.zip *.gba *.agb *.md *.bin *.gen);;MAME romset (CPS1) (*.zip);;Game Boy Advance (*.gba *.agb);;Mega Drive (*.md *.bin *.gen);;All (*.*)",
    "Pasta com as ROMs (CPS1)":
        "Folder with the ROMs (CPS1)",
    "Pasta de saída":
        "Output folder",
    "\nSem SoundFont: marque “SoundFont (.sf2)”.":
        "\nNo SoundFont: check “SoundFont (.sf2)”.",
    "\nBiblioteca de patches FM do jogo: vai junto para a conversão.":
        "\nThe game's FM patch library: goes along to the conversion.",
    "Cada efeito toca só nos canais marcados. Tons que cabem no PSG usam os tons do PSG marcados; o que não couber (ou se nenhum tom do PSG estiver marcado) vai para os FM marcados, como onda quadrada. Efeitos que soam ao mesmo tempo no jogo (ex.: pulo e tiro) devem ficar em canais diferentes. O ruído sempre usa o 4º canal do PSG. Deixe livres os canais que a música usa (o FM 6 costuma ser o PCM do XGM).":
        "Each effect plays only on the checked channels. Tones that fit on the PSG use the checked PSG tones; whatever doesn't fit (or if no PSG tone is checked) goes to the checked FM channels, as a square wave. Effects that sound at the same time in the game (e.g. jump and shot) should be on different channels. The noise always uses the 4th PSG channel. Leave free the channels the music uses (FM 6 is usually the XGM PCM).",
    "Padrão do jogo":
        "Game default",
    "Opções do Neo Geo":
        "Neo Geo options",
    "As amostras ADPCM do Neo Geo viram PCM do XGM (8 bits, 14 kHz), gravadas dentro de cada música — é o que mais pesa na ROM. O relatório de cada música mostra quantos KB ela usa.":
        "The Neo Geo ADPCM samples become XGM PCM (8-bit, 14 kHz), stored inside each song — this is what weighs most in the ROM. Each song's report shows how many KB it uses.",
    "Chimbais e pratos no ruído do PSG (economiza PCM e ROM)":
        "Hi-hats and cymbals on the PSG noise (saves PCM and ROM)",
    "ADPCM-B (instrumento com altura) no FM 5, com o timbre tirado da amostra":
        "ADPCM-B (pitched instrument) on FM 5, with the timbre taken from the sample",
    "Duração máxima de cada amostra:":
        "Maximum length of each sample:",
    "Volume gravado na amostra, em passos de:":
        "Volume stored in the sample, in steps of:",
    "Notas mais baixas que (ecos) ficam de fora:":
        "Notes quieter than this (echoes) are left out:",
    "Padrão":
        "Default",
    "Arquivo…":
        "File…",
    "Pasta…":
        "Folder…",
    "Jogo CPS1:":
        "CPS1 game:",
    "Todas":
        "All",
    "Nenhuma":
        "None",
    "Qualidade do SF2 (CPS1):":
        "SF2 quality (CPS1):",
    "Repetições do loop (CPS1):":
        "Loop repetitions (CPS1):",
    "Escolha o jogo CPS1 na lista e clique em Carregar.":
        "Choose the CPS1 game in the list and click Load.",
    "Jogo de Mega Drive sem perfil": "Mega Drive game without a profile",
    "Quer que o programa analise a ROM e tente montar o perfil sozinho? Leva de 1 a 3 minutos; confira as prévias depois.":
        "Do you want the program to analyze the ROM and try to build the profile by itself? It takes 1 to 3 minutes; check the previews afterwards.",
    "Perfil criado: {0} música(s). Carregando o jogo…": "Profile created: {0} song(s). Loading the game…",
    "Análise sem resultado: ": "Analysis found nothing: ",
    "Escolha primeiro a ROM (.zip, .gba, .md ou pasta).":
        "First choose the ROM (.zip, .gba, .md or folder).",
    "1 faixa":
        "1 track",
    "Selecione ao menos uma música.":
        "Select at least one song.",
    "Opções do Neo Geo salvas.":
        "Neo Geo options saved.",
    "{0} MIDI(s) em {1}":
        "{0} MIDI(s) in {1}",
    "Gere também o SoundFont (.sf2) para converter.":
        "Also generate the SoundFont (.sf2) to convert.",
    "Divide canal com":
        "Shares a channel with",
    "  (deixe livres os canais das vozes e golpes do jogo)":
        "  (leave free the channels for the game's voices and hits)",
    "3 dB (mais variações, mais ROM)":
        "3 dB (more variations, more ROM)",
    "12 dB (menos ROM)":
        "12 dB (less ROM)",
    "Marque ao menos um canal PCM para a música.":
        "Check at least one PCM channel for the music.",
    "{0} jogos CPS1 + jogos de GBA com o motor de som da Nintendo + Irem (Moon Patrol, Kung-Fu Master, Lode Runner), Ghosts 'n Goblins, Nemesis e Neo Geo (KOF 94 a 98, Garou), direto para o Mega Drive":
        "{0} CPS1 games + GBA games with Nintendo's sound engine + Irem (Moon Patrol, Kung-Fu Master, Lode Runner), Ghosts 'n Goblins, Nemesis and Neo Geo (KOF 94 to 98, Garou), straight to the Mega Drive",
    "Erro ao carregar":
        "Error while loading",
    "Aviso: ":
        "Warning: ",
    "Canais dos efeitos — {0}":
        "Effect channels — {0}",
    "Marque ao menos um canal para:\n":
        "Check at least one channel for:\n",
    "{0} faixas":
        "{0} tracks",
    "Canais dos efeitos salvos para ":
        "Effect channels saved for ",
    "{0} música(s) em XGM/VGM, {1} efeito(s) com código C e {2} voz(es) em WAV em {3}\nJá estão prontos para o Mega Drive: não precisam passar pela aba Conversão.":
        "{0} song(s) in XGM/VGM, {1} effect(s) with C code and {2} voice(s) in WAV in {3}\nThey are ready for the Mega Drive: they don't need to go through the Conversion tab.",
    "Efeito":
        "Effect",
    "Omitir (não tocar)":
        "Omit (don't play)",
    "Ferramentas — conversores e efeitos":
        "Tools — converters and effects",
    "Música VGM (AY / YM2203)":
        "VGM music (AY / YM2203)",
    "Efeitos de chip (AY)":
        "Chip effects (AY)",
    "Efeitos e vozes (WAV)":
        "Sound effects and voices (WAV)",
    "Banco de patches .tfi":
        ".tfi patch bank",
    "O andamento e os relatórios aparecem no registro da janela principal.":
        "Progress and reports show up in the main window's log.",
    "Arquivos VGM / VGZ (AY-3-8910 ou YM2203)":
        "VGM / VGZ files (AY-3-8910 or YM2203)",
    "VGM com AY-3-8910 ou YM2203":
        "VGM with AY-3-8910 or YM2203",
    "Pasta onde gravar XGM, VGM e prévias":
        "Folder to save XGM, VGM and previews",
    "Travar o andamento na grade de 60 Hz (muda o andamento em até 6%)":
        "Lock the tempo to the 60 Hz grid (changes the tempo by up to 6%)",
    "Ajusta o andamento para o passo rítmico durar um número inteiro de quadros\n(ritmo sem oscilação no XGM). Desmarcado = andamento original.":
        "Adjusts the tempo so the rhythmic step lasts a whole number of frames\n(steady rhythm in the XGM). Unchecked = original tempo.",
    "Começar na primeira nota (tira o silêncio do início)":
        "Start at the first note (removes the silence at the start)",
    "Destino de cada canal do AY-3-8910":
        "Destination of each AY-3-8910 channel",
    "Vale para todos os arquivos da lista (no YM2203, só o SSG passa por aqui e segue o automático). \"Automático\": tom que cabe no PSG fica no PSG; parte grave (abaixo de ~109 Hz) ou do 2º AY vai para o FM como onda quadrada.":
        "Applies to every file in the list (on the YM2203, only the SSG goes through here and follows automatic). \"Automatic\": a tone that fits on the PSG stays on the PSG; a low part (below ~109 Hz) or one from the 2nd AY goes to FM as a square wave.",
    "Analisar o 1º arquivo":
        "Analyze the 1st file",
    "Mostra quais canais tocam no 1º arquivo da lista e para onde o automático os mandaria.":
        "Shows which channels play in the 1st file of the list and where automatic would send them.",
    "Tudo automático":
        "All automatic",
    "Converter":
        "Convert",
    "Efeitos em VGM de AY-3-8910 (um efeito por arquivo)":
        "Effects in AY-3-8910 VGM (one effect per file)",
    "VGM de AY-3-8910 com o efeito":
        "AY-3-8910 VGM with the effect",
    "Pasta onde gravar os efeitos":
        "Folder to save the effects",
    "Tom {}":
        "Tone {}",
    "Canais PSG livres:":
        "Free PSG channels:",
    "Canais FM livres:":
        "Free FM channels:",
    "Marque os canais que a música do jogo deixa livres para efeitos. Os tons do efeito ocupam os canais PSG marcados, em ordem; tons graves demais para o PSG vão para os FM marcados, como onda quadrada. O FM 6 costuma estar com o PCM do XGM. Sai .vgm, prévia .wav e um .c para o SGDK com nome_play() e nome_update().":
        "Check the channels that the game's music leaves free for effects. The effect's tones take the checked PSG channels, in order; tones too low for the PSG go to the checked FM channels, as a square wave. FM 6 is usually taken by the XGM PCM. Outputs .vgm, a .wav preview and a .c for SGDK with name_play() and name_update().",
    "WAVs de efeitos sonoros e vozes":
        "Sound effect and voice WAVs",
    "WAVs de efeitos e vozes":
        "Effect and voice WAVs",
    "Pasta onde gravar os WAVs prontos para o SGDK":
        "Folder to save the WAVs ready for SGDK",
    "Taxa do driver:":
        "Driver rate:",
    "Comprimir levemente a dinâmica (menos chiado nos finais; recomendado)":
        "Slightly compress the dynamics (less hiss at the tails; recommended)",
    "Manter o volume relativo entre os arquivos (mesmo ganho para todos)":
        "Keep the relative volume between files (same gain for all)",
    "Marcado: um passo continua mais baixo que uma explosão (os sons fracos ficam com um pouco mais de chiado).\nDesmarcado: cada som no volume máximo.":
        "Checked: a footstep stays quieter than an explosion (quiet sounds get a little more hiss).\nUnchecked: each sound at maximum volume.",
    "Preparar":
        "Prepare",
    "Pasta com os arquivos .tfi (subpastas incluídas)":
        "Folder with the .tfi files (subfolders included)",
    "Pasta onde gravar o banco (de preferência vazia)":
        "Folder to save the bank (preferably empty)",
    "Pasta com os .tfi:":
        "Folder with the .tfi files:",
    "Pasta do banco:":
        "Bank folder:",
    "Junta patches .tfi de jogos e packs (subpastas incluídas) num banco sem repetidos. Ao terminar, o campo \"Banco de patches prontos\" da janela principal passa a apontar para ele. Milhares de patches levam alguns minutos.":
        "Gathers .tfi patches from games and packs (subfolders included) into a bank without duplicates. When it finishes, the main window's \"Ready-made patch bank\" field points to it. Thousands of patches take a few minutes.",
    "Montar banco":
        "Build bank",
    "Adicionar…":
        "Add…",
    "Remover":
        "Remove",
    "Limpar":
        "Clear",
    ";;Todos (*.*)":
        ";;All (*.*)",
    "Adicione ao menos um arquivo à lista.":
        "Add at least one file to the list.",
    "Escolha a pasta de saída.":
        "Choose the output folder.",
    "Canal do AY":
        "AY channel",
    "No automático (1º arquivo)":
        "Automatic (1st file)",
    "Marque ao menos um canal livre (PSG ou FM).":
        "Check at least one free channel (PSG or FM).",
    "Escolha a pasta dos .tfi e a pasta do banco.":
        "Choose the .tfi folder and the bank folder.",
    "Analisando {0}":
        "Analyzing {0}",
    "Destinos repetidos:\n\n":
        "Repeated destinations:\n\n",
    "Rotas escolhidas: ":
        "Chosen routes: ",
    "não toca":
        "doesn't play",
    "PSG tom {0}":
        "PSG tone {0}",
    "AY{0} — canal {1}":
        "AY{0} — channel {1}",
    "({0}: não usa esta tabela)":
        "({0}: doesn't use this table)",
    "os instrumentos deste SoundFont são de um jogo extraído, não de um banco General MIDI: cada instrumento do MIDI vai soar como o timbre do jogo que tiver o mesmo número":
        "the instruments of this SoundFont come from an extracted game, not from a General MIDI bank: each MIDI instrument will sound like the game voice with the same number",
    "o SoundFont não tem kit de bateria General MIDI (banco 128)":
        "the SoundFont has no General MIDI drum kit (bank 128)",
    "o “kit” deste SoundFont são as amostras do Oki de um jogo CPS1 (bateria misturada com vozes e efeitos), não um kit General MIDI":
        "this SoundFont's “kit” is the Oki samples of a CPS1 game (drums mixed with voices and effects), not a General MIDI kit",
    "no kit de bateria deste SoundFont todas as peças tocam a mesma amostra":
        "in this SoundFont's drum kit every piece plays the same sample",
    "Esta música está sem SoundFont: os instrumentos e a bateria vêm do banco General MIDI padrão do programa. A bateria usa o kit padrão; os instrumentos começam com timbres genéricos — gere candidatos e aprove os patches FM normalmente.":
        "This song has no SoundFont: instruments and drums come from the program's default General MIDI bank. The drums use the default kit; the instruments start with generic timbres — generate candidates and approve the FM patches as usual.",
    "o kit de bateria deste SoundFont não tem {0} das {1} peças que a música usa":
        "this SoundFont's drum kit is missing {0} of the {1} pieces the song uses",
    "O SoundFont escolhido não é compatível com a bateria desta música: {0}.\n\nSerá usado o kit de bateria padrão do programa (General MIDI). Os outros instrumentos continuam vindo do SoundFont.":
        "The chosen SoundFont isn't compatible with this song's drums: {0}.\n\nThe program's default drum kit (General MIDI) will be used. The other instruments still come from the SoundFont.",
    "Atenção: {0}. Se este MIDI não veio da aba Extração, deixe o campo SoundFont vazio para usar o banco General MIDI padrão.":
        "Warning: {0}. If this MIDI didn't come from the Extraction tab, leave the SoundFont field empty to use the default General MIDI bank.",
    "aprovado":
        "approved",
    "provisório":
        "provisional",
    "bateria (PCM)":
        "drums (PCM)",
    "baixo":
        "bass",
    "melodia":
        "melody",
    "harmonia":
        "harmony",
    "bateria":
        "drums",
    "eco":
        "echo",
    "Música VGM (AY / YM2203 / Mega Drive)":
        "VGM music (AY / YM2203 / Mega Drive)",
    "Arquivos VGM / VGZ (AY-3-8910, YM2203 ou Mega Drive)":
        "VGM / VGZ files (AY-3-8910, YM2203 or Mega Drive)",
    "VGM com AY-3-8910, YM2203 ou Mega Drive":
        "VGM with AY-3-8910, YM2203 or Mega Drive",
    "Escolher: volta para":
        "Choose: loop back to",
    "Põe um loop num VGM que não tem, ou muda o que ele tem. Os tempos contam do começo da música convertida (depois de tirar o silêncio do início, se essa opção estiver marcada).":
        "Adds a loop to a VGM that has none, or changes the one it has. Times count from the start of the converted song (after removing the silence at the start, if that option is checked).",
    "fim da música":
        "end of song",
    "ao chegar a":
        "when reaching",
    "  (desmarcado = o loop do próprio VGM)":
        "  (unchecked = the VGM's own loop)",
    "Em qual dos 4 canais PCM do XGM vão as amostras do DAC de um VGM do Mega Drive. Os outros ficam livres para os efeitos do jogo.":
        "Which of the 4 XGM PCM channels gets the DAC samples of a Mega Drive VGM. The others stay free for the game's sound effects.",
    "Canal PCM (VGM do Mega Drive):":
        "PCM channel (Mega Drive VGM):",
    "O fim do loop tem de vir depois do início.":
        "The loop end must come after the start.",
    "Arquivos VGM / VGZ (AY-3-8910, YM2203 ou Mega Drive) ou XGM":
        "VGM / VGZ files (AY-3-8910, YM2203 or Mega Drive) or XGM",
    "VGM com AY-3-8910, YM2203 ou Mega Drive, ou XGM do SGDK":
        "VGM with AY-3-8910, YM2203 or Mega Drive, or SGDK XGM",
    "bumbo (modo bateria)":
        "bass drum (rhythm mode)",
    "tom (modo bateria)":
        "tom (rhythm mode)",
    "Destino de cada canal (AY-3-8910 e OPL)":
        "Destination of each channel (AY-3-8910 and OPL)",
    "OPL (YM3526, YM3812, Y8950): \"Automático\" manda as vozes mais importantes e as graves para os 6 canais FM; as que sobram vão para o PSG, como onda quadrada (só se não descerem abaixo de ~110 Hz). Escolha aqui quem fica no FM, quem vai para o PSG e quem não toca.":
        "OPL (YM3526, YM3812, Y8950): \"Automatic\" sends the most important voices and the low ones to the 6 FM channels; the rest go to the PSG as a square wave (only if they don't go below ~110 Hz). Choose here who stays on FM, who goes to the PSG and who doesn't play.",
    "Canal do OPL":
        "OPL channel",
    "canal {0}":
        "channel {0}",
    "OPL {0} e {1} → {2}":
        "OPL {0} and {1} → {2}",
    "Música VGM (AY / YM2203 / OPL / Mega Drive)":
        "VGM music (AY / YM2203 / OPL / Mega Drive)",
    "Arquivos VGM / VGZ (AY-3-8910, YM2203, YM2610, OPL ou Mega Drive) ou XGM":
        "VGM / VGZ files (AY-3-8910, YM2203, YM2610, OPL or Mega Drive) or XGM",
    "VGM com AY-3-8910, YM2203, YM2610, OPL ou Mega Drive, ou XGM do SGDK":
        "VGM with AY-3-8910, YM2203, YM2610, OPL or Mega Drive, or SGDK XGM",
    "Arquivos VGM / VGZ (AY-3-8910, YM2203, OPL ou Mega Drive) ou XGM":
        "VGM / VGZ files (AY-3-8910, YM2203, OPL or Mega Drive) or XGM",
    "VGM com AY-3-8910, YM2203, OPL ou Mega Drive, ou XGM do SGDK":
        "VGM with AY-3-8910, YM2203, OPL or Mega Drive, or SGDK XGM",
    "Tamanho do XGM": "XGM size",
    "Tamanho do XGM: {0}": "XGM size: {0}",
    "Qualidade máxima": "Maximum quality",
    "Equilibrado": "Balanced",
    "Compacto": "Compact",
    "Só tira o que não muda o som (escritas repetidas, amostras idênticas, silêncio).":
        "Removes only what doesn't change the sound (repeated writes, identical samples, silence).",
    "Arquivo um pouco menor, som igual ao ouvido (recomendado).":
        "Slightly smaller file, sounds the same to the ear (recommended).",
    "Arquivo bem menor: amostras mais curtas e juntadas; pequena perda nas batidas longas.":
        "Much smaller file: shorter, merged samples; slight loss on long drum hits.",
    "amostras: {0} → {1}": "samples: {0} → {1}",
    " ({0} junta(s))": " ({0} merged)",
    "{0} escrita(s) repetida(s) removida(s)": "{0} repeated write(s) removed",
    "tamanho ({0}): ": "size ({0}): ",
    "Volume das amostras": "Sample volume",
    "Volume do FM fixo": "Fixed FM volume",
    "Algoritmo e feedback": "Algorithm and feedback",
    "Copiar de": "Copy from",
    "Deixa a música com as características técnicas de outra (XGM ou VGM do Mega Drive): marque abaixo o que copiar. Vale para todos os arquivos da lista; o original nunca é sobrescrito.":
        "Gives the song the technical characteristics of another one (XGM or Mega Drive VGM): check below what to copy. Applies to every file in the list; the original is never overwritten.",
    "XGM ou VGM do Mega Drive de referência": "Reference XGM or Mega Drive VGM",
    "Ver": "Show",
    "Mostra o que a referência tem (volume das amostras, algoritmos, LFO).":
        "Shows what the reference has (sample volume, algorithms, LFO).",
    "O pico das amostras fica igual ao da referência (um ganho só para todas).":
        "The sample peak matches the reference (one gain for all of them).",
    "Cada instrumento toca sempre no volume mais forte que já usava: somem acentos, ecos e fades.":
        "Each instrument always plays at the loudest volume it already used: accents, echoes and fades go away.",
    "Algoritmo e feedback de cada canal FM iguais aos da referência. Muda o timbre: os operadores continuam os da música.":
        "Algorithm and feedback of each FM channel match the reference. Changes the timbre: the operators stay the song's own.",
    "LFO global e sensibilidade AMS/FMS de cada canal iguais aos da referência.":
        "Global LFO and per-channel AMS/FMS sensitivity match the reference.",
    "Estilo de referência:": "Reference style:",
    "Escolha o arquivo de referência do estilo.": "Choose the style reference file.",
    "Marque pelo menos uma característica para copiar.": "Check at least one characteristic to copy.",
    "Estilo de referência: ": "Reference style: ",
    "Música de referência do estilo": "Style reference song",
    "A referência de estilo tem de ser um XGM ou um VGM do Mega Drive: {0}":
        "The style reference must be an XGM or a Mega Drive VGM: {0}",
    "{0}: pico das amostras {1}, algoritmo/feedback {2}, LFO {3}": "{0}: sample peak {1}, algorithm/feedback {2}, LFO {3}",
    "ligado": "on",
    "desligado": "off",
    "algoritmo/feedback da referência: {0}": "reference algorithm/feedback: {0}",
    "volume do FM fixo: {0} → {1} mudanças de volume": "fixed FM volume: {0} → {1} volume changes",
    "LFO da referência: {0}": "reference LFO: {0}",
    "volume das amostras: pico {0} → {1} ({2:+.1f} dB)": "sample volume: peak {0} → {1} ({2:+.1f} dB)",
    "estilo de {0}: ": "style of {0}: ",
    "Transbordo para o PSG: nota que sumiria por falta de canal PCM é imitada no PSG":
        "PSG overflow: a note that would vanish for lack of a PCM channel is imitated on the PSG",
    "Quando o canal PCM está ocupado por uma nota que acabou de começar, a nova vai para um tom livre do PSG (onda quadrada na nota da amostra; bumbo e tons com a altura caindo; caixa no ruído, se ele estiver livre). Vozes e efeitos continuam no PCM. Soa mais simples que a amostra, mas melhor que silêncio — útil com um canal PCM só.":
        "When the PCM channel is busy with a note that has just started, the new one goes to a free PSG tone (square wave at the sample's pitch; bass drum and toms with falling pitch; snare on the noise, if it is free). Voices and effects stay on PCM. Simpler than the sample, but better than silence — useful with a single PCM channel.",
    "Neo Geo (YM2610):": "Neo Geo (YM2610):",
    "Canais PCM, chimbais no ruído, ADPCM-B no FM, duração e volume das amostras. São as mesmas opções da aba Extração: mudar aqui muda lá também.":
        "PCM channels, hi-hats on the noise, ADPCM-B on FM, sample length and volume. These are the same options as the Extraction tab: changing them here changes them there too.",
    "PCM {0}; amostras até {1:.1f} s; volume em passos de {2:.0f} dB": "PCM {0}; samples up to {1:.1f} s; volume in {2:.0f} dB steps",
    "Tamanho do XGM:": "XGM size:",
    "Onde gravar os arquivos convertidos?": "Where should the converted files go?",
    "Pasta de saída: {0}": "Output folder: {0}",
    "Na pasta de saída": "In the output folder",
    "Numa pasta nova dentro dela…": "In a new folder inside it…",
    "Nome da pasta a criar dentro de\n{0}:": "Name of the folder to create inside\n{0}:",
    "Digite o nome da pasta.": "Type the folder name.",
    "O nome não pode ter estes caracteres: < > : \" / \\ | ? *": "The name cannot contain these characters: < > : \" / \\ | ? *",
    "Não consegui criar a pasta:\n{0}": "Could not create the folder:\n{0}",
    "Uma pasta para cada música (com os arquivos dela dentro)": "One folder per song (with its files inside)",
    "sem atenuação": "no attenuation",
    "Atenuar o PCM (DAC):": "Attenuate PCM (DAC):",
    "Abaixa o volume das amostras PCM (DAC) — bateria, vozes — em relação ao FM e ao PSG, quando elas soam altas demais. 6 dB = metade do volume. Vale para todos os chips (Mega Drive, Neo Geo…) e é gravado nas amostras do XGM.":
        "Lowers the volume of the PCM (DAC) samples — drums, voices — relative to FM and PSG, when they sound too loud. 6 dB = half the volume. Applies to every chip (Mega Drive, Neo Geo…) and is baked into the XGM samples.",
    'Perfis do Mega Drive…': 'Mega Drive profiles…',
    'Salvar o perfil deste jogo para compartilhar, importar um perfil que alguém mandou ou abrir a pasta dos seus perfis.': "Save this game's profile to share it, import a profile someone sent you or open your profiles folder.",
    'Salvar o perfil deste jogo para compartilhar…': "Save this game's profile to share…",
    'Colocar o perfil deste jogo na pasta do projeto': "Put this game's profile in the project folder",
    'Importar perfil que recebi…': 'Import a profile I received…',
    'Abrir a pasta dos meus perfis': 'Open my profiles folder',
    'Salvar perfil': 'Save profile',
    'Perfil (*.json)': 'Profile (*.json)',
    'Perfil salvo': 'Profile saved',
    'Perfil salvo em:\n{0}\n\nMande esse arquivo para quem mantém o conversor (ou para um amigo, que usa "Importar perfil que recebi…").': 'Profile saved to:\n{0}\n\nSend this file to the converter\'s maintainer (or to a friend, who uses "Import a profile I received…").',
    'Perfil copiado para a pasta do projeto: ': 'Profile copied to the project folder: ',
    'Importar perfil': 'Import profile',
    'Perfil importado': 'Profile imported',
    '{0} perfil(is) importado(s). Abra a ROM do jogo na aba Extração.': "{0} profile(s) imported. Open the game's ROM in the Extraction tab.",
    'Perfis do Mega Drive': 'Mega Drive profiles',
    'Ouça as prévias; se ficou bom, compartilhe pelo botão "Perfis do Mega Drive…" → Salvar o perfil deste jogo.': 'Listen to the previews; if it sounds right, share it with the "Mega Drive profiles…" button → Save this game\'s profile.',
    'Exportar todos os meus perfis…': 'Export all my profiles…',
    'Exportar todos os meus perfis para…': 'Export all my profiles to…',
    'Perfis exportados': 'Profiles exported',
    '{0} perfil(is) copiado(s) para:\n{1}\n\nCompacte essa pasta e mande para quem mantém o conversor.': "{0} profile(s) copied to:\n{1}\n\nZip this folder and send it to the converter's maintainer.",
    'Não testado': 'Not tested',
    'Analisado, falta ouvir': 'Analyzed, not heard yet',
    'Funciona': 'Works',
    'Não funciona': "Doesn't work",
    'Marque aqui se cada jogo funcionou no conversor: clique na bolinha (cinza = não testado, verde = funciona, vermelho = não funciona; amarelo = analisado, falta ouvir). Depois exporte a lista em CSV e mande para quem mantém o conversor.': "Mark here whether each game worked in the converter: click the dot (grey = not tested, green = works, red = doesn't work; yellow = analyzed, not heard yet). Then export the list as CSV and send it to the converter's maintainer.",
    'Confirmar': 'Confirm',
    'Vincular ao jogo selecionado': 'Link to the selected game',
    'A ROM aberta na aba Extração é o jogo selecionado na lista.': 'The ROM open in the Extraction tab is the game selected in the list.',
    'Não está na lista…': 'Not in the list…',
    'Adiciona o jogo da ROM aberta à lista e vincula.': "Adds the open ROM's game to the list and links it.",
    'Todos': 'All',
    'Buscar nome, franquia ou observação…': 'Search name, franchise or note…',
    'Tipo:': 'Type:',
    'Franquia:': 'Franchise:',
    'Situação:': 'Status:',
    'Todas': 'All',
    'Nome do jogo': 'Game name',
    'Franquia': 'Franchise',
    'Região': 'Region',
    'Tipo': 'Type',
    'Perfil': 'Profile',
    'Observação': 'Note',
    'Adicionar jogo…': 'Add game…',
    'Exportar também os não testados': 'Also export untested games',
    'Exportar CSV…': 'Export CSV…',
    'análise: ': 'analysis: ',
    '{0} jogos · {1} funcionam · {2} não funcionam · {3} falta ouvir · {4} não testados': "{0} games · {1} work · {2} don't work · {3} not heard yet · {4} not tested",
    'Escrever observação…': 'Write a note…',
    'Vincular a ROM aberta a este jogo': 'Link the open ROM to this game',
    'Desfazer vínculos com ROMs': 'Remove ROM links',
    'Remover da lista (adicionado por você)': 'Remove from the list (added by you)',
    'Nenhuma ROM de Mega Drive aberta na aba Extração. Abra uma lá para vincular ao jogo daqui, ou marque os jogos direto na lista.': 'No Mega Drive ROM open in the Extraction tab. Open one there to link it to a game here, or mark the games directly in the list.',
    'ROM aberta: {0} (código {1}, checksum {2})': 'Open ROM: {0} (code {1}, checksum {2})',
    'é o jogo: {0}': 'it is: {0}',
    'parece ser: {0}. Confirma?': 'looks like: {0}. Confirm?',
    'não reconheci o jogo: selecione-o na lista e clique em "Vincular ao jogo selecionado".': 'game not recognized: select it in the list and click "Link to the selected game".',
    'Catálogo': 'Catalog',
    'Selecione primeiro o jogo na lista.': 'Select the game in the list first.',
    'Adicionar jogo': 'Add game',
    'Nome do jogo:': 'Game name:',
    'Exportar CSV': 'Export CSV',
    '{0} linha(s) gravada(s) em:\n{1}\n\nMande esse arquivo para quem mantém o conversor.': "{0} row(s) written to:\n{1}\n\nSend this file to the converter's maintainer.",
    'Catálogo Mega Drive': 'Mega Drive catalog',
    'Oficial': 'Official',
    'Tradução': 'Translation',
    'Hack': 'Hack',
    'Protótipo': 'Prototype',
    'Não oficial': 'Unlicensed',
    'Homebrew': 'Homebrew',
    'Multijogos': 'Multi-game',
    'Extra': 'Extra',
    'Furnace (.fur)': 'Furnace (.fur)',
    'Prévia (.wav)': 'Preview (.wav)',
    'Para o SGDK / jogo de Mega Drive.': 'For SGDK / a Mega Drive game.',
    'Para tocadores de VGM e para estudar o som.': 'For VGM players and studying the sound.',
    'Para editar no Furnace Tracker.': 'To edit in Furnace Tracker.',
    'Para ouvir o resultado no computador.': 'To listen to the result on the computer.',
    'Marque ao menos um tipo de arquivo para gerar (XGM, VGM, Furnace ou Prévia).': 'Tick at least one file type to generate (XGM, VGM, Furnace or Preview).',
    'Arquivos de cada música (Mega Drive, Neo Geo, arcades):': 'Files for each song (Mega Drive, Neo Geo, arcades):',
    'Gerar:': 'Generate:',
}
