# Blunder Tutor

[![codecov](https://codecov.io/gh/MrLokans/chess-blunder-trainer/badge.svg)](https://codecov.io/gh/MrLokans/chess-blunder-trainer) [![prek](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/j178/prek/master/docs/assets/badge-v0.json)](https://github.com/j178/prek)

## Fork Notice (Hygros Version)

This repository is a forked and extended version of the original project by [MrLokans](https://github.com/MrLokans/chess-blunder-trainer).

Key changes in this fork include:

- Added LLM-based lesson explanations in the analysis pipeline
- Added database migrations and schema support for refutations and LLM explanation versioning
- Added background backfill jobs for generating explanations on existing analysis data
- Improved trainer and game review UX (result cards, eval bar handling, drag behavior, continue-play flow)
- Extended API/service layers to expose richer analysis and explanation data
- Updated tests and locale content to cover the new behavior

## README

For full product documentation, setup instructions, and feature overview, please read the original upstream README:

https://github.com/MrLokans/chess-blunder-trainer#readme
