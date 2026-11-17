"""Conversão direta de VGM com AY-3-8910 (arcades como Moon Patrol, MSX, Spectrum…) para o Mega Drive.

O AY e o PSG do Mega Drive (SN76489) são chips parecidos (onda quadrada, ruído, 16 níveis de
volume), então a música é traduzida registrador a registrador, quadro a quadro — sem MIDI.
Ver :mod:`mdconv.ay.convert`.
"""
from mdconv.ay.convert import AyConversion, convert_file  # noqa: F401
