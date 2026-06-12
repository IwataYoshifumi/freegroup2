# freegroup2

Source-available business card organizer

## Overview

FreeGroup2 is a business-support application built around business card management. Uploaded card images flow through a four-layer data model — OriginalImage → BusinessCard → Contact → Person — separating raw scans, recognized cards, normalized contact data, and consolidated people. The project is under active development: core data models are complete through v1.5, and v1.6 email-delivery features are in preparation.

OCR runs via the Claude API by default; for the external worker route (`OCR_BACKEND=worker_cowork`), the operator prompt for the worker (Cowork) is [`cards/prompts/worker_cowork_ocr_prompt.txt`](cards/prompts/worker_cowork_ocr_prompt.txt).

## Roadmap

- **v1.6**: email delivery with click tracking.
- **Future**: starting with FreeGroup2 for Sales, expanding into a groupware suite covering email delivery, project management, scheduling, and facility reservation.

## License

FreeGroup2 is source-available software provided under the FreeGroup2 License v1.0. Internal use within your own organization is free of charge; commercial use requires registration. Modification is permitted and may be kept fully private, while redistribution is not allowed. See [`LICENSE.md`](LICENSE.md) for the full terms.

## Contributing

Contributions are accepted only from registered businesses or their affiliated persons. Before submitting a pull request, you must agree to the applicable Contributor License Agreement:

- Individual contributors: [`CLA-individual.md`](CLA-individual.md) (ICLA)
- Corporate contributors: [`CLA-corporate.md`](CLA-corporate.md) (CCLA)

See [`LICENSE.md`](LICENSE.md) for details on the license and contribution model.

## Contact

Network Tokai Co., Ltd. (株式会社ネットワーク東海)
Representative: Yoshifumi Iwata (岩田好史)
Toyota, Aichi, Japan
Website: https://www.network-tokai.jp/
