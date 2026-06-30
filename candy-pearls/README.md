# Candy Pearls

> AI-powered candy reward system for kids over Signal — the model handles conversation and pricing, Home Assistant stays the bank.

![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

Each child has a pearl balance as a proxy currency for sweets. The child does
not message Signal directly — parents, grandparents, or other relatives write
in a dedicated Signal group per child, reporting what the child wants or
already received. Claude figures out the price (or asks), and Home Assistant
atomically debits the right child's balance. No pearl ever leaves without HA
saying so.

See the **Documentation** tab for setup instructions and the full
configuration reference.

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
