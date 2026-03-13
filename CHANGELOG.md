# Changelog

## [0.2.0](https://github.com/pythoninthegrass/icarus/compare/icarus-v0.1.0...icarus-v0.2.0) (2026-03-13)


### Features

* add `check` subcommand for pre-flight validation ([91c0868](https://github.com/pythoninthegrass/icarus/commit/91c08686a2278526e4fd0c331fb1e68cd3ef2b40))
* **cli:** add `import` subcommand to adopt existing Dokploy projects ([9ead2d7](https://github.com/pythoninthegrass/icarus/commit/9ead2d7fdcee92ef4ddf0876595d57cb9d935bb8))
* **cli:** add logs and exec commands via docker-py over SSH ([acd16f0](https://github.com/pythoninthegrass/icarus/commit/acd16f030a669a8010abbab92f3225d5aeb99548))
* **cli:** add unified `deploy` subcommand, rename old deploy to `trigger` ([d489377](https://github.com/pythoninthegrass/icarus/commit/d489377291af0a832375a3efcf36782f02b773f6))
* **cli:** restructure repo for uv tool install with `dps` entry point ([116290a](https://github.com/pythoninthegrass/icarus/commit/116290a7e32431e68ff8510688e4d11e54b6fdd8))
* implement all 7 prod gaps in dokploy.py (buildType, domain.path, autoDeploy, watchPaths, replicas, triggerType, buildPath) ([970f9ea](https://github.com/pythoninthegrass/icarus/commit/970f9eaa85c6abe64f52a0d8074e32d0afc16ff3))
* **setup:** auto-select GitHub provider by matching owner ([edefe14](https://github.com/pythoninthegrass/icarus/commit/edefe14aeb83b912e75926be179e997473244491))


### Bug Fixes

* **api:** always send dockerContextPath/dockerBuildStage in saveBuildType ([31b8037](https://github.com/pythoninthegrass/icarus/commit/31b8037074f46d323ef85329cc3290da0e09bf82))
* **api:** update dokploy.py for Dokploy v0.28.4 compatibility ([50895a8](https://github.com/pythoninthegrass/icarus/commit/50895a8b2a484713a27b52ab8600381d3e8c8bc2))
* apply principle of least privilege to release-please.yml permissions ([fa04d9b](https://github.com/pythoninthegrass/icarus/commit/fa04d9b0e2527fe38d6e40e1cde75e450228d32a))
* **cli:** show help and exit 0 when no command is provided ([7a91a10](https://github.com/pythoninthegrass/icarus/commit/7a91a100745349654aec08364dbfee1ae6a84bd8))
* **config:** handle missing .env file at import time ([76fb573](https://github.com/pythoninthegrass/icarus/commit/76fb573e01e4847a455367d24dc9dff2af911296))
* **config:** read .env from CWD instead of installed package location ([4f42a45](https://github.com/pythoninthegrass/icarus/commit/4f42a450e5294a33836c063ff8b11e27f9df4fd6))
* **deploy:** detect orphaned state and recreate project ([adcfbfd](https://github.com/pythoninthegrass/icarus/commit/adcfbfd276fdca5c8d29c9db79d0c8203030ee11))
* **env:** resolve {app_name} placeholders in filtered .env ([2ecf31f](https://github.com/pythoninthegrass/icarus/commit/2ecf31ffd578f4aa7d0aeecdd8c5815f55f222ce))
* **test:** add pytestmark to test_unit.py and test_integration.py ([9d89eed](https://github.com/pythoninthegrass/icarus/commit/9d89eedc0d21bd21db00520f2d8b7827d724641f))
* **test:** assert domain fields against fixture, not hardcoded values ([dfba11a](https://github.com/pythoninthegrass/icarus/commit/dfba11a60ef718b1ed9df370dd983ab49293d02a))
* **test:** monkeypatch decouple config instead of os.environ ([5768053](https://github.com/pythoninthegrass/icarus/commit/5768053a2c328d3e7a8d867533394f949c521f88))


### Documentation

* add backlog tasks ([e2d70bf](https://github.com/pythoninthegrass/icarus/commit/e2d70bf1dabae51550d4738ffc52782fe14d3fc1))
* add logo to README with attribution ([14b86b4](https://github.com/pythoninthegrass/icarus/commit/14b86b4da1ec54c9a1125928350fb7a679c9a003))
* Fix GitHub issues link in security response plan ([ba0f40b](https://github.com/pythoninthegrass/icarus/commit/ba0f40bb98726507b45c7036b173ce80300269cf))
* rename to icarus (ic) ([12f5a7a](https://github.com/pythoninthegrass/icarus/commit/12f5a7a59ea2c1db1207b7ab044ec03af6af0b90))
* update backlog tasks ([afa734e](https://github.com/pythoninthegrass/icarus/commit/afa734eae63cda625e3b147fdb79a73c475340ea))
* update llm instrx ([191ce50](https://github.com/pythoninthegrass/icarus/commit/191ce5060070db8dd70e049d0485cf17908e6880))
* update llm instrx ([65f41f6](https://github.com/pythoninthegrass/icarus/commit/65f41f69e76c3e14c97853ebcebcfd23578bba68))
