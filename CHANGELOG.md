# Changelog

## [0.3.0](https://github.com/pythoninthegrass/icarus/compare/icarus-v0.2.0...icarus-v0.3.0) (2026-07-01)


### Features

* add `plan` subcommand with terraform-style diff view ([3aba161](https://github.com/pythoninthegrass/icarus/commit/3aba1616b199eda817d8baf5e07a2db8e5a70edd))
* add API retry with exponential backoff to DokployClient ([87cf836](https://github.com/pythoninthegrass/icarus/commit/87cf83640a0ec5df737a5731227479e1465b3be3))
* add app config drift detection to plan and reconcile on redeploy ([f89cec4](https://github.com/pythoninthegrass/icarus/commit/f89cec4749c64eee869ae2c09e979e0655b7ec66))
* add backup destination and database backup management ([#15](https://github.com/pythoninthegrass/icarus/issues/15)) ([8145f87](https://github.com/pythoninthegrass/icarus/commit/8145f870006802420aa5b8b2da9caffd6b6e0e2b))
* add basic auth security support for applications ([f90825c](https://github.com/pythoninthegrass/icarus/commit/f90825cef2ebab6df1067fb28a29cdfeab8835cd))
* add basic auth security support for applications ([2a24aa4](https://github.com/pythoninthegrass/icarus/commit/2a24aa4a176fcb89ae2e033e030f9e14ccbc874b))
* add compose deployment support, archive completed backlog tasks ([6054205](https://github.com/pythoninthegrass/icarus/commit/605420554b7e5f575b83a687f87c3d91693f2ac7))
* add Conductor workspace automation for python/uv/mise ([c71e111](https://github.com/pythoninthegrass/icarus/commit/c71e1112879e1cbcb6577a222f6077990c8973be))
* add container registry management support ([fd1c5fe](https://github.com/pythoninthegrass/icarus/commit/fd1c5feccfab4457704adb7ec8335d8f3cb48bdd))
* add custom certificate support ([2f880ea](https://github.com/pythoninthegrass/icarus/commit/2f880eac3b08a7d197f37c981215529d2f30bcc1))
* add database resource management (postgres, mysql, mariadb, mongo, redis) ([616307d](https://github.com/pythoninthegrass/icarus/commit/616307d9d48296c7c5ff1c6ecd09a6f8e60c58b3))
* add docker-compose example with env var substitution, backlog task ([06cfa99](https://github.com/pythoninthegrass/icarus/commit/06cfa99a75702f9987d1623448df92821d2bb969))
* add port exposure support (TCP/UDP port mappings) ([7bbec40](https://github.com/pythoninthegrass/icarus/commit/7bbec4030cc30a5039db72a16193d4d57510faca))
* add standalone `clean` command, reuse in `destroy` ([ce2ed00](https://github.com/pythoninthegrass/icarus/commit/ce2ed00c52a21ac627010ff3e88450dc5c4bdbef)), closes [#5](https://github.com/pythoninthegrass/icarus/issues/5)
* add URL redirect support for applications ([#12](https://github.com/pythoninthegrass/icarus/issues/12)) ([cacec6d](https://github.com/pythoninthegrass/icarus/commit/cacec6ddd1a1d524064fe1d12f9a46d5fbb6035f))
* **ci:** add workflow to monitor Dokploy upstream releases ([1bc8414](https://github.com/pythoninthegrass/icarus/commit/1bc8414aeaedd8fdec55fefa71be4f4ca0ce027d))
* **compose:** support sourceType: github for compose apps ([6d1f37c](https://github.com/pythoninthegrass/icarus/commit/6d1f37cd807bdf5e3548b7df9b60efed7f54a0aa)), closes [#18](https://github.com/pythoninthegrass/icarus/issues/18)
* **config:** add volume mount support to dokploy.yml ([3b6c6ef](https://github.com/pythoninthegrass/icarus/commit/3b6c6ef36c3690471d513a0a9b67c99c92bc4c62)), closes [#7](https://github.com/pythoninthegrass/icarus/issues/7)
* **deploy:** use application.redeploy for existing projects and clean up stale routes ([d7c2131](https://github.com/pythoninthegrass/icarus/commit/d7c213196d003f3f5c74174ba9b66ccb0e9786a0))
* reconcile domains on apply/redeploy ([c68c4cc](https://github.com/pythoninthegrass/icarus/commit/c68c4ccea2b2b0dacbcde958fdbd00a26515b8c4))
* reconcile mounts/volumes on apply/redeploy ([248312a](https://github.com/pythoninthegrass/icarus/commit/248312a2b813584ddd36955a1e279c7349b1234f))
* **schedule:** add cron job support for applications ([7e1f690](https://github.com/pythoninthegrass/icarus/commit/7e1f690d11340e4b3f8370048e97ed4afae5a5fa))
* support DOTENV_FILE env var and --env-file flag for env push ([5e4036e](https://github.com/pythoninthegrass/icarus/commit/5e4036e846a204bd91f2f4d4473b873fed54cb1d)), closes [#8](https://github.com/pythoninthegrass/icarus/issues/8)


### Bug Fixes

* **config:** use serviceId instead of applicationId for mounts.create ([6464a02](https://github.com/pythoninthegrass/icarus/commit/6464a020ec0935c90b62e94c0e59aadcc1fe3fc2))
* **env:** deduplicate keys when merging per-app env into shared env ([21bcfa6](https://github.com/pythoninthegrass/icarus/commit/21bcfa6f78a61eac83b9542165680b02097835a2))
* **env:** merge per-app env into shared push for env_targets apps ([bfaf0de](https://github.com/pythoninthegrass/icarus/commit/bfaf0de4995336e355730d9459308b21c05bd432)), closes [#19](https://github.com/pythoninthegrass/icarus/issues/19)
* **logs:** prefer running container over exited in select_container ([1246df6](https://github.com/pythoninthegrass/icarus/commit/1246df68e40ea88af99e5772da2db0f1656674f1))
* **monitor:** fetch_openapi.sh uses download_url for specs &gt;1MB ([f0be724](https://github.com/pythoninthegrass/icarus/commit/f0be72412eaaad9cc07fd56258bf670ea7f16393)), closes [#21](https://github.com/pythoninthegrass/icarus/issues/21)
* **mounts:** use serviceId instead of applicationId for mounts.create ([28e337d](https://github.com/pythoninthegrass/icarus/commit/28e337da52c5594279939327f58281fad46f1a8c))
* remove legacy dokploy_seed reference ([6ff8205](https://github.com/pythoninthegrass/icarus/commit/6ff8205cdb178930f3c12f00c0552ae5a1235e29))
* **setup:** save state early to prevent orphaned resources on failure ([8ff4c00](https://github.com/pythoninthegrass/icarus/commit/8ff4c0057b85f465cb15235558c16e4728883ef9))
* **trigger:** sync env into live Docker service spec on redeploy ([c435478](https://github.com/pythoninthegrass/icarus/commit/c43547848f547053c2d1ef1328ef71f4c4a41a9f)), closes [#20](https://github.com/pythoninthegrass/icarus/issues/20)


### Documentation

* add schedule API notes, redeploy endpoint, missing fixtures and isStaticSpa ([1039382](https://github.com/pythoninthegrass/icarus/commit/1039382947171e8a2e0575e1b170b6f5da8dafaf))
* **agents:** add Version Control section for issue-closing commit trailers ([98bf4aa](https://github.com/pythoninthegrass/icarus/commit/98bf4aa5c548a1dc04804486fef39bc5fc420801))
* **agents:** expand section comment prohibition to include horizontal rule style ([10e1364](https://github.com/pythoninthegrass/icarus/commit/10e1364310efcb4e75bb968d06a05bd3e90f6b2e))
* Merge pull request [#14](https://github.com/pythoninthegrass/icarus/issues/14) from pythoninthegrass/pythoninthegrass/basic-auth-security ([29ae047](https://github.com/pythoninthegrass/icarus/commit/29ae047e0675830e83b8570133d6b9278cdc3307))
* **readme:** update commands table and project structure ([eb52646](https://github.com/pythoninthegrass/icarus/commit/eb52646495d7e55480f0461a2ae2adc87a75e00d))
* update backlog tasks ([332b0c1](https://github.com/pythoninthegrass/icarus/commit/332b0c1fd4674b97f7949889fffb83d1e2e69874))
* update backlog tasks ([3dba86b](https://github.com/pythoninthegrass/icarus/commit/3dba86ba8e163c4212ff7b454c627273f6070015))
* update backlog tasks ([b5fe000](https://github.com/pythoninthegrass/icarus/commit/b5fe000d8138a394c9f5b1cd703b36fda75c28bf))
* update llm instrx ([c475d12](https://github.com/pythoninthegrass/icarus/commit/c475d1200f9500b29e21ff09b4296854b1596231))

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
