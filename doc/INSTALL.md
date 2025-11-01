# Installing the LavaGui App

## macOS

The app comes as a macOS DMG file.

The app is not signed so the DMG file will need to be blessed to allow it to be
opened. This is the process:

1.  Download the DMG file.

2.  Use whatever virus checker you have on it. Can't hurt.

3.  Locate the DMG file in a terminal. If it has been downloaded via a
    browser, it will probably be in the `~/Downloads` directory.

4.  Remove the quarantine from the DMG:    
    `xattr -rd com.apple.quarantine lavagui-<VERSION>-macos-arm64.dmg`

5.  Either double click the DMG in the Finder or run:    
    `open lavagui-<VERSION>-macos-arm64.dmg`

6.  Drag the lava GUI app icon to the `Applications` folder (or wherever you
    want to install it).

## Windows

The app comes as an unsigned Windows install package
`lavagui-<VERSION>-windows-x64.exe`.

1. Download the installer.
2. Use whatever virus checker you have on it. Can't hurt.
3. Open the installer and follow the prompts.
