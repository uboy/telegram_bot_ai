# Initialize repository and sync code
1. create a root directory for all projects. The directory will contain directories for individual projects like ohos_master, ohos_weekly, upstream, etc. e.g. mkdir ~/proj;cd proj, mkdir /data/<special>/ohos;cd /data/<special>/ohos.
1. link a directory to shared downloaded prebuilts (to save time for downloading) inside "project's root": `ln -s  /data/shared/openharmony_prebuilts openharmony_prebuilts`
1. create project directory inside "project's root" directory created on first step
1. Obtain code for trunk (add --depth 1 for faster sync, https faster):
   - ssh: `repo init -u git@gitcode.com:openharmony/manifest.git -b master --no-repo-verify --reference=/data/shared/ohos_mirror`
   - https: `repo init -u https://gitcode.com/openharmony/manifest.git -b master --no-repo-verify --reference=/data/shared/ohos_mirror`
     -  `--reference=/data/shared/ohos_mirror` - use this option for local repo mirror (it is synced every 15 minutes) to achieve better time of code sync
   - weekly branch: `repo init -u https://gitee.com/openharmony/manifest.git -b weekly --no-repo-verify --depth 1`
     - To get other than latest weekly:
       1. Go to https://gitee.com/openharmony/manifest/commits/weekly
       1. Find needed weekly release
       1. Use corresponding commit SHA (can be copied by pressing copy button on right side) from release in `-b` option.
          For example, use `-b 2d5eb0a123fba92af51401edf2218bc3a4921bc2` for `weekly_20240219`.
   - OpenHarmony_ArkUI_Upstream_2024 branch:
        `repo init -u https://gitee.com/openharmony/manifest.git -b OpenHarmony_ArkUI_Upstream_2024 --no-repo-verify --depth 1`
         then local mirror can be used too - add `--reference=/data/shared/ohos_mirror`
   -  To initialize mirror:
      -  `repo init -u dmazur@tsnnlx12bs01:/data/shared/ohos_mirror/manifest.git -b master --no-repo-verify --mirror`
      -  `repo init -u git@gitcode.com:openharmony/manifest.git -b master --no-repo-verify --mirror`
      -  `repo init -u https://gitcode.com/openharmony/manifest.git -b master --no-repo-verify --mirror`
      -  `cd /data/shared/ohos_mirror; if [ ! -f .repo_sync_inprogress ]; then date>.repo_sync_inprogress;/usr/bin/repo sync > sync.log 2>sync.errors;export reposyncresult=$?;repo forall -j 8 -c 'git lfs fetch --all --prune';date>last_sync_date;echo $reposyncresult>>last_sync_date;rm .repo_sync_inprogress; else echo "sync in progress" ; fi`
   -  Mirror of openharmony on gitcode:
      -  `https://gitcode.com/openharmony/arkui_ace_engine/overview`
   -  To Sync fast `--depth 1`
1. Sync code, do not set many jobs as it can cause reject from Gitee.com
   - `repo sync -c -j 8` # number of jobs shall be chosen depending on your network speed and load on Gitee.com
     - if you got error ``, just start sync again in few minutes
     - if some repo cannot sync/checkout, try to fetch the repo with `--depth 1`, e.g. `git fetch -j 8 --depth 1`
     - sync or die: `result=1 && while [ $result -ne 0 ]; do repo sync -v -c -j 12 && result=$?; done`
     - use `git fetch origin --unshallow` in your repo for getting a history
1. Pull large files from repositories
   `repo forall -j 8 -c 'git lfs pull'`
1. Download prebuilts
   `build/prebuilts_download.sh`
   - to use shared prebuilts don't forget  make link inside "project's root": `ln -s  /data/shared/openharmony_prebuilts openharmony_prebuilts`
1. Original article and releases can be found on [Gitee.com](https://gitee.com/openharmony/docs/blob/master/en/device-dev/get-code/sourcecode-acquire.md)

# Build
## SDK
1. Build SDK:
   `./build.sh --product-name ohos-sdk --ccache  --gn-args=sdk_build_arkts=true`
   Additional params:
   1. `-j <n>` # number of jobs shall be chosen depending on your build machine (number of cores, `cat /proc/cpuinfo`)
   1. `--gn-args sdk_platform=win` # Build only for Windows
   1. `--gn-args sdk_platform=linux` # Build only for Linux
   1. `--fast-rebuild` # fast rebuild
## rk3568
1. Build rk3568: `./build.sh --product-name rk3568 --ccache`
   - Fast Rebuild: `./build.sh --product-name rk3568 --ccache --fast-rebuild`
1. Output can be found in `<project dir>/out/rk3568/packages/phone/images`
## xhd100
1. `./build.sh --product-name xhd100 --ccache`
   - `./build.sh --product-name xhd100 --ccache --fast-rebuild`
1. Output can be found in `<project dir>/out/xhd100/packages/phone/images`
## Single component
1. `./build.sh --product-name ohos-sdk --gn-args sdk_platform=win --build-target <single component name>`
1.  Possible components are: **ace_packages**, **ace_engine**, **input**, **graphic_2d** etc
    -  `./build.sh --product-name ohos-sdk --gn-args sdk_platform=win --build-target ace_packages --fast-rebuild`
## Linux unittests
1. `./build.sh --product-name rk3568 --ccache --no-prebuilt-sdk --build-target linux_unittest`

## MacOS
1.  Make sure you’re using MacOS SDK 13 (or earlier). With SDK 14+ there is an issue on the SDK side:
[Issue on Apple Developers Forum](https://forums.developer.apple.com/forums/thread/764223)
   - Download [SDK](https://github.com/alexey-lysiuk/macos-sdk/tree/main/MacOSX13.3.sdk)
   - unpack to `/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs`
   - make aliase `MacOSX13.3.sdk->MacOSX13.sdk`
   - `sudo xcode-select -switch /Applications/Xcode.app`
1. Install packages:
   ```
   brew install python
   brew install npm
   brew install ccache
   brew install repo
   brew install openjdk
   brew install git-lfs
   git lfs install
   ```
1. Optional??? Install [repo tool](https://gitee.com/mazurdenis/open-harmony/wikis/Environment/How%20to%20prepare%20OHOS%20environment#install-repo)
1. Init and sync repository
   ```
   repo init -u https://gitee.com/openharmony/manifest.git -b <branch name> --no-repo-verify --depth 1
   repo sync -c -j 8
   repo forall -c 'git lfs pull'
   ./build/prebuilts_download.sh --disable-rich
   ```
1. Optional? Disable unverified developers , go to Settings -> Privacy&Security -> Allow installs from anywhere
   ```
   sudo spctl --master-disable
   ```
1. Build:
   ```
   ./build.sh --product-name ohos-sdk --gn-args sdk_platform=mac --ccache
   ```
   You may face a problem with third_party/ffmpeg build, so just remove this dependency from ide/tools/previewer 

## Tips&Tricks
1. `--fast-rebuild` - flag for fast rebuild of target, if you made small changes without full sync
1. Reset whole repo:
   ```
   repo forall -c 'git clean -fxd'
   repo forall -c 'git reset --hard HEAD'
   repo forall -c 'git lfs pull'
   ```
1. If after run compilation of the code pnpm install freeze like:
![Image description](https://foruda.gitee.com/images/1737975859298683825/8c0f8e4a_13678028.jpeg "freeze_installing_pnpm.jpg")
Change "builds.sh" file:
```
vim ./build.sh
```
and commit line:
```
#npm install --silent > /dev/null
```
as on the example:
![Image description](https://foruda.gitee.com/images/1737975880414896346/6698b9a8_13678028.jpeg "fix_freezing_pnpm_intall.jpg")
1. if build fails with error below or similar:
   ```
    [OHOS ERROR] ../../foundation/arkui/ace_engine/frameworks/bridge/declarative_frontend/engine/jsi/jsi_declarative_engine.cpp:2073:52: error: no viable conversion from 'function<bool (const std::basic_string<char> &, bool, unsigned char **, unsigned long *, std::basic_string<char> &)>' to 'function<bool (std::string, uint8_t **, size_t *, std::string &)>'
    [OHOS ERROR]     panda::JSNApi::SetHostResolveBufferTracker(vm, std::move(callback));
    [OHOS ERROR]                                                    ^~~~~~~~~~~~~~~~~~~
    [OHOS ERROR] /usr/lib/gcc/x86_64-linux-gnu/12/../../../../include/c++/12/bits/std_function.h:375:7: note: candidate constructor not viable: no known conversion from 'typename std::remove_reference<function<bool (const basic_string<char> &, bool, unsigned char **, unsigned long *, basic_string<char> &)> &>::type' (aka 'std::function<bool (const std::basic_string<char> &, bool, unsigned char **, unsigned long *, std::basic_string<char> &)>') to 'std::nullptr_t' for 1st argument
    [OHOS ERROR]       function(nullptr_t) noexcept
   ```
   make sure you have `gcc-11` installed, path valid and exists `/usr/lib/gcc/x86_64-linux-gnu/11`. Rename `/usr/lib/gcc/x86_64-linux-gnu/12` to `/usr/lib/gcc/x86_64-linux-gnu/12.bak` and restart the build
## Tests
1.  [Test case building](https://gitee.com/openharmony/docs/blob/master/en/device-dev/device-test/developer_test.md#test-case-building)
## Misc
1. logs can be found at `./out/<product-name>/build.log`
1. Original article and other build commands can be found on [Gitee.com](https://gitee.com/openharmony/build)
   1. [How To Write, Build & Run TC's](https://gitee.com/openharmony/docs/blob/master/en/device-dev/device-test/developer_test.md)