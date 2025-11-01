
# Useful Links

*   [Lava User Guide](https://jin-gizmo.github.io/lava/)

*   [Lava GUI on GitHub](https://github.com/jin-gizmo/lava-gui)

# Genesis

**Lava** was developed at [Origin Energy](https://www.originenergy.com.au)
as part of the *Jindabyne* initiative. While not part of our core IP, it proved
valuable internally, and we're sharing it in the hope it's useful to others.

Kudos to Origin for fostering a culture that empowers its people
to build complex technology solutions in-house.

Check out [Jin Gizmo](https://jin-gizmo.github.io) on GitHub.

# Accessing a Lava Realm in AWS

The app relies on the same configuration and credentials to access a lava realm
in AWS that the AWS CLI uses. The app will present a list of the profiles
specified in your `~/.aws/credentials` file and allow you to select one. This
will then connect you to the lava realms in that AWS account to which you have
access.

If you haven't authenticated to an AWS account, as represented by a credentials
profile, an error message will be presented when you try to select it. You will
need to authenticate to AWS and then select the profile again.

# App Configuration

The app can use a small configuration file `~/.lava/lava.cfg` with the following
items under the `GUI` key. Reasonable defaults are provided for all of them.

| Key | Description                                                  |
|:----|:------------------------------------------------------------ |
| `aws_profile`   | The AWS profile name in `~/.aws/credentials`  and `~/.aws/config` to connect to AWS APIs. This will be updated as the user switches profiles in the GUI to remember the most recently used value so there is no need to enter it manually. |
| `code_font` | The font used for JSON objects (logs, job specifications etc.). A monospaced font is recommended. |
| `code_font_size` | The font size for JSON objects. |
| `current_theme` | The GUI theme selected by the user in the app. There is no need to enter it manually |
| `details_font_size` | The font size for general text components. |
| `expander_icon` | The icon to use to indicate expansion components (accordions). The names can be chosen from the [Flet icon gallery](https://gallery.flet.dev/icons-browser/). Case is not significant. |
| `heading_font_size` | The font size for heading components. |
| `https_proxy`   | Use the specified proxy URL to connect to AWS.               |
| `json_indent` | The number of spaces to indent JSON (job specifications. job logs etc.) |
| `window_height` | Window height in pixels, including title bar, window chrome etc. |
| `window_width` | Window width in pixels, including window chrome etc. |

To reset any item to its default value, delete it from the configuration file.

Sample file showing default values:

```properties
[GUI]
aws_profile = 
code_font = Consolas
code_font_size = 12
current_theme = Dark Theme
details_font_size = 11
expander_icon = keyboard_arrow_down
heading_font_size = 12
https_proxy =
json_indent = 4
markdown_indent = 4
window_height = 800
window_width = 1400
```
