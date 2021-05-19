---
title: "Maestral's dependencies: desktop-notifier"
permalink: /blog/python-desktop-notifier
tags: development dependencies
excerpt: >
  Desktop notifications are a great way to keep users informed of file changes and sync
  issues, especially when running a sync daemon without a GUI. This blog post covers
  differences in native platform APIs and outlines how and why I wrote my own Python
  module for cross-platform desktop notifications.
related: true
---

Desktop notifications are a great way to keep users informed of file changes and sync
issues, especially when running a sync daemon without a GUI or system tray icon. There
are a number of existing Python libraries to send desktop notifications. Most only work
on specific platforms and require compiled extension modules (for example
[win10toast](https://github.com/jithurjacob/Windows-10-Toast-Notifications)) others such
as [plyer](https://github.com/kivy/plyer) are cross-platform and purely Python. However,
none of them managed to provide what I needed for Maestral:

1. Pure Python code base without compiled extension modules. Plyer is the only library
   which fulfilled this requirement.
2. Ability to show buttons and react to user interactions. This requires integration with
   an event loop such as asyncio, the Glib main loop or Core Foundation's `CFRunLoop`. To
   my knowledge, none of the existing Python libraries supports this.
3. Ability to manage and remove already sent notifications.
4. Use up-to-date platform APIs: All of the Python libraries currently use the
   deprecated `NSUserNotificationCenter` API on macOS.

I therefore ended up writing my own module and eventually spun it off as a separate
library [desktop-notifier](https://github.com/SamSchott/desktop-notifier). This post gives
a brief introduction to native platforms APIs and the choices which I had to make when
writing an abstraction layer.

## Platform APIs

Let's start with introducing the different platforms to see what we'll need to work
with.

### Linux

Most Linux desktop environments, including Gnome and KDE, implement the
[Desktop Notifications Specification](https://developer.gnome.org/notification-spec/). It
defines a basic DBus API to send desktop notifications and all implementations must
support basic features such as showing an app icon, a title and a message. Anything
beyond that, notably buttons (or "actions" in the language of the spec) may or may not be
supported with varying limitations. The documentation is riddled with statements such as

> Clients should try and avoid making assumptions about the presentation and abilities of
  the notification server.

At least we have a single, convenient API to work with and an easy way to query the
capabilities of a notification server. Python also has several DBus libraries that
integrate with common event loops and some don't even require compiled extension modules.

Many Python packages which don't want to use the DBus API directly instead rely on the
`notify-send` command line tool, available with most distributions. Since this requires
calling an external executable, it is not possible to listen to DBus signals and react
to user interactions with the notification when using `notify-send`.

### macOS

The macOS API has been transitioning over the past few releases from the now deprecated
[NSUserNotificationCenter](https://developer.apple.com/documentation/foundation/nsusernotificationcenter)
to the more modern [UNUserNotificationCenter](https://developer.apple.com/documentation/usernotifications/unusernotificationcenter)
library.

The former was introduced in OS X Mountain Lion and is currently still available in
macOS 11. The latter is available on both macOS and iOS and introduces new capabilities
such as an unlimited number of buttons, previews of notification attachments and providing
completely custom views. You can therefore show almost any user interface inside a
desktop notification, for instance a full game of [Flappy Bird](https://t.co/LlMx2AjvHH)
🐥

Because both iOS and macOS give users fine-grained control over which apps are allowed
to send which types of desktop notifications, only signed app bundles or frameworks can
send notifications. This prevents a (potentially compromised) app from impersonating any
other app or circumventing the user's settings.

This introduces some difficulties when sending notifications from a Python interpreter.
In practice, the `UNUserNotificationCenter` API can only be used from a signed framework
build of Python, as available from [python.org](python.org), but not from a regular
build as provided for instance by Homebrew. In addition, notifications will always
appear to come from "Python" unless the module has been bundled and distributed as a
standalone app.

The `UNUserNotificationCenter` library can be accessed via
[ctypes](https://docs.python.org/3/library/ctypes.html) or more conveniently by using a
Python to Objective-C bridge such as [PyObjC](https://github.com/ronaldoussoren/pyobjc) or
[Rubicon Objective-C](https://beeware.org/project/projects/bridges/rubicon/).

### Windows

Windows, with its history of different GUI libraries, is a bit more complicated. Good old
`win32` provides the
[Shell_NotifyIconW](https://docs.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw)
API which can accessed over FFI / ctypes and can even show modern "toast" notifications
on Windows 10. It is however limited to just showing an icon, a title and a message.
Popular Python packages such as `win10toast` use this approach.

Richer features such as image previews, interactive buttons, etc, are currently only
available through the Windows Runtime APIs which can only be access using pre-compiled
Python packages such as [Python/WinRT](https://github.com/Microsoft/xlang/tree/master/src/package/pywinrt/projection).

## Python implementation

Dealing with those very different native APIs, I was faced with a number of choices for
the abstraction layer:

### Sync vs async

On the surface, this seems like an easy choice. A synchronous API can be called from
any function and does not require spinning an event loop. It is therefore more
convenient to the user of the library. When looking at the problem more closely,
through, many native API are actually asynchronous. This certainly holds for the DBus API.
`UNUnserNotificationCenter` similarly exposes mostly asynchronous methods that take a
callable "completion handler" as an argument which will be called with a result after
the async call has completed (e.g., after the notification has been scheduled). In
addition, dealing with user interactions requires spinning an event loop anyways. An
asynchronous API is therefore the more natural choice.

### Supported features

We have seen previously that the available features vary significantly between
platforms, desktop environments and different APIs on the same platform. Do we want to
support only the smallest common denominator of features? Or can we live with a
"leaky" abstraction which exposes features that might not work on some platforms?
And how do we handle unsupported options?

I decided to live with a leaky abstraction and to ignore any provided arguments such as
buttons, custom icons or sounds, when not supported by a platform. Instead of raising an
exception, we will only emit a warning.

An overview of supported features in the desktop-notifier library is given
[here](https://desktop-notifier.readthedocs.io/en/latest/background/platform_support.html).

### Event loop intgeration

We know that some form of event loop integration will be necessary to handle user
interaction. Do we want to integrate directly with platform-native event loops such Apple's
[CFRunLoop](https://developer.apple.com/documentation/corefoundation/cfrunloop-rht) on
or the [GLib main loop](https://developer.gnome.org/glib/stable/glib-The-Main-Event-Loop.html)
in Gtk?

I opted for the easier but less convenient solution of only supporting Python's asyncio
event loop. Users can then choose to integrate the asyncio event loop directly with native
event loops themselves if required. On macOS or iOS, this can be done using
[Rubicon Objective-C](`https://rubicon-objc.readthedocs.io/en/latest/how-to/async.html#integrating-asyncio-with-corefoundation`):

```python
import asyncio
from rubicon.objc.eventloop import EventLoopPolicy

# Install the event loop policy
asyncio.set_event_loop_policy(EventLoopPolicy())
```

Integration with the Glib main loop can be done using
[asyncio-glib](https://github.com/jhenstridge/asyncio-glib/):

```python
import asyncio
import asyncio_glib

# Install the event loop policy
asyncio.set_event_loop_policy(asyncio_glib.GLibEventLoopPolicy())
```

### Native bridges

I chose [python-dbus-next](https://python-dbus-next.readthedocs.io) as a DBus library
because of the excellent asyncio integration and because of pure Python 3 code base.

Similarly, I opted for
[Rubicon Objective-C](https://beeware.org/project/projects/bridges/rubicon/) as a bridge
to Objective-C because it written purely in Python and because it is much more convenient
than using ctypes directly.

desktop-notifier currently does not support Windows, partially because I am still
undecided whether to rely on extension modules or just use ctypes and sacrifice
functionality. However, an implementation using
[Python/WinRT](https://github.com/Microsoft/xlang/tree/master/src/package/pywinrt/projection)
is currently in development.

## Example usage

The final library with all of its features and limitations is documented
[here](https://desktop-notifier.readthedocs.io). It enables you to send a simple desktop
notification with buttons, a reply field and callbacks with just a few lines of code:

{% include figure
image_path="/assets/images/macOS-desktop-notifier.gif"
alt="desktop-notifier on macOS"
%}

The above examples uses the following code:

```python
import asyncio
import platform
from desktop_notifier import DesktopNotifier, Button, ReplyField


notifier = DesktopNotifier()


async def main():

    await notifier.send(
        title="Julius Caesar",
        message="Et tu, Brute?",
        buttons=[
            Button(
                title="Mark as read",
                on_pressed=lambda: print("Marked as read"),
            ),
        ],
        reply_field=ReplyField(
            title="Reply",
            button_title="Send",
            on_replied=lambda text: print("Brutus replied:", text),
        ),
        on_clicked=lambda: print("Opening chat app"),
    )


if platform.system() == "Darwin":

    # Integrate with CFRunLoop.
    from rubicon.objc.eventloop import EventLoopPolicy

    asyncio.set_event_loop_policy(EventLoopPolicy())


# Schedule main function and run event loop.
loop = asyncio.get_event_loop()
loop.create_task(main())
loop.run_forever()
```

If you like the approach taken by this library and would like to help with developing the
Windows backend, do get in touch!
