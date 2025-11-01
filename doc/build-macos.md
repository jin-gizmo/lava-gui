# Building the Lava GUI on macOS

The instructions explain how to package the lava GUI as an unsigned macOS app.
If you want to pay Apple US$99 per year to get it signed, knock yourself out.

> [!NOTE]
> A word of caution... Flet can be a bit susceptible to corruption of the build
> environment. If you get build / run errors that look to be coming from Flet
> internals, remove the `build` directory and the virtual environment (`venv`)
> and start afresh.

## Prerequisites

Building the GUI as a macOS app has these prerequisites:

1. An M-series Mac.

2. Python 3.11+

3. Xcode

4. [Ruby](#ruby)

5. [CocoaPods](#cocoapods)

6. [Flutter](#flutter)

7. [Flet](#flet)

To check that the prerequisites are setup correctly, you can run (and rerun)
`make preflight` to check things out.

### Ruby

Flutter uses [CocoaPods](https://cocoapods.org) for the build process. This in
turn requires Ruby. An old version of Ruby is supplied as part of macOS. You can
*try* using that to build the lava GUI, but if you run into trouble, try
installing a new version:

```bash
brew install ruby
```

Note that brew will not overwrite the system version of Ruby. You will need to
adjust PATH to put `/opt/homebrew/opt/ruby/bin` earlier in the PATH than
`/usr/bin`.

### CocoaPods

With Ruby installed, the [CocoaPods](https://cocoapods.org) gem can be
installed. It it recommended to install for the current user to avoid needing
root permissions:

```bash
export GEM_HOME="$HOME/.gem"
export PATH="$GEM_HOME/bin:$PATH"
gem install cocoapods --no-document

# See if it worked
pod --version
```

> ![INFO]
> The `GEM_HOME` and `PATH` environment variable settings shown above **must**
> be in place for the rest of the install and build process. If you are using
> the Makefile, it will set these for you when building the app. You will need
> to set them yourself when installing CocoaPods.

### Flutter

Install Flutter from Homebrew.

```bash
brew install flutter
flutter --version
```

You should see something like this:

```bare
Flutter 3.32.5 • channel stable • https://github.com/flutter/flutter.git
Framework • revision fcf2c11572 (10 days ago) • 2025-06-24 11:44:07 -0700
Engine • revision dd93de6fb1 (11 days ago) • 2025-06-24 07:39:37 -0700
Tools • Dart 3.8.1 • DevTools 2.45.1
```

Run the following commands

```bash
# First, stop flutter from phoning home
flutter --disable-analytics

# Check if our installation is healthy
flutter doctor
```

You should see something like this:

```bare
Doctor summary (to see all details, run flutter doctor -v):
[✓] Flutter (Channel stable, 3.32.5, on macOS 15.4.1 24E263 darwin-arm64, locale en-AU)
[✗] Android toolchain - develop for Android devices
      ... More loser spam ... ignore it.

[!] Xcode - develop for iOS and macOS (Xcode 16.3)
    ✗ Unable to get list of installed Simulator runtimes.
[✓] Chrome - develop for the web
[!] Android Studio (not installed)
[✓] Connected device (2 available)
[✓] Network resources
```

> The crucial bit here is **develop for ... macOS**. This indicates the install
> is fine.

### Flet

Flet should already be installed in the virtual environment as a result of the
`make init` process. If not:

```bash
source venv/bin/activate
pip install flet
# Check ...
flet --version
flet doctor
```

### Pandoc

[Pandoc](https://pandoc.org) is used to generate HTML versions of the documents
included in the DMG distribution bundle.

Install pandoc thus:

```bash
brew install pandoc
```

## Building the app

### Preparation

```bash
# Clone the repo
git clone git@github.com:jin-gizmo/lava-gui.git

# Setup a virtual environment and install python dependencies
make init

# Activate the venv
source venv/bin/activate
```

### Makefile Build Targets

The key `Makefile` build targets are described below. As always, `make help` is your friend.

| Target    | Description                                                  |
| --------- | ------------------------------------------------------------ |
|           | Print help.                                                  |
| help      | Print help.                                                  |
| init      | Initialise the virtual environment. Idempotent.              |
| preflight | Checks the prerequisites are in place. It's a good idea to run this first. |
| build     | Build the app `dist/macos/Lava.app` (x86 and ARM)            |
| strip     | Strip x86 binaries from the app to leave `dist/macos/Lava.app` (ARM only) |
| dmg       | Build the app install bundle `dist/macos/lavagui-<VERSION>-macos-arm64.dmg` |
| app       | Shortcut for `make build strip dmg`                          |
| clean     | Delete `dist/macos` and its contents.                        |

### The Build

The short version is:

```bash
# Check we're ready to build
make preflight
# Build the app, strip to only include ARM components and package as DMG
make app
```

The DMG should appear as `dist/macos/lavagui-<VERSION>-macos-arm64.dmg`.

### Nitty Gritty

With all of the above [prerequisites](#Prerequisites) rigmarole out of the way,
the GUI app can be built:

```bash
# First make sure our prerequisites look ok
make preflight
# Build the app
make build
```

This will take a while . Eventually, you should see something like this:

```bare
╭──────────────────────────────────────────────────────────────────────────────╮
│ Successfully built your macOS bundle! 🥳 Find it in dist/macos directory. 📁 │
╰──────────────────────────────────────────────────────────────────────────────╯
```

The app should appear as `dist/macos/Lava.app`, which, like all macOS apps, is a
directory. You can run the app at this point, either by opening `dist/macos` in
the Finder and double clicking on the app, or:

```bash
open dist/macos/Lava.app
```

The app will be a universal binary containing `arm64` and `x86_64` versions. The
latter is almost never needed now and just takes up space. To slim it down to
`arm64` only:

```bash
make strip
```

To package the app for distribution as an Apple DMG:

```bash
make dmg
```

You can shortcut all of this by doing:

```bash
make app
```

The DMG should appear as `lavagui-<VERSION>-macos-arm64.dmg`.
