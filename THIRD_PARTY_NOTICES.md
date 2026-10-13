# Third-party notices

TalkAbout project code and documentation are provided under [MIT](LICENSE), Copyright (c) 2026 yjj3019. This grant does not replace or relicense third-party components.

Runtime dependencies listed in pyproject.toml and requirements.lock.txt retain their own upstream licenses and notices. Installed wheel dist-info/license files are preserved in the app virtualenv and copied with it into the image; do not strip them when redistributing. Dependency versions and license metadata should be inventoried for each release. An absent metadata expression is unknown, not proof of MIT licensing.

Official Python and PostgreSQL base images, CPython/PostgreSQL, Debian packages, binary libraries and container tooling remain governed by their respective upstream licenses. Their existing files and notices are not overwritten by the project LICENSE. See [Python Official Image](https://hub.docker.com/_/python), [PostgreSQL Official Image](https://hub.docker.com/_/postgres), and each distribution's shipped copyright/license files.

No third-party code or legal notice is removed by this change. The MIT image label describes TalkAbout itself, not every dependency or operating-system package. Before distributing a release, preserve the included notices and review any additional distribution obligations.
