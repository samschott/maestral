---
title: Inotify limits
permalink: /docs/inotify-limits
---

On Linux, the sync daemon uses the **inotify** facility of the kernel to be notified of
changes to local files and folders. Inotify provides fine-grained information about the
creation, modification and deletion of files but only allows you to watch a limited
number of folders because each "watch" requires a small amount memory. The limit holds
for all applications that subscribe to be notified of file system events, including IDEs
and other sync clients.

There are in fact two limits which are important to us:

1.  **max_user_watches**: This is the maximum number of folders which can be watched
    for file changes. Subfolders are counted separately so that a local Dropbox folder
    with five subfolders would need six inotify watches in total.

    Since the introduction of the inotify in 2005, `max_user_watches` is set to 8192 by
    default. Available memory has increased significantly since then and Linux kernels
    since version 5.11 will automatically adjust `max_user_watches` up to 1048576
    depending on the available RAM. Linux 5.11 was released in February 2021 and at the
    time of writing, most distributions still use an older kernel version.

2.  **max_user_instances**: This is the maximum number of inotify instances allowed for
    each user. Most processes will only create a single instance but some may create
    multiple. This value defaults to 128 for most distributions.

You can check the current limits on your system with `sysctl`:

```shell
$ sysctl fs.inotify
fs.inotify.max_queued_events = 16384
fs.inotify.max_user_instances = 128
fs.inotify.max_user_watches = 524288
```

If you hit either of those limits, the sync daemon will log an error message and the GUI
will show a notification with brief instructions on how to increase the limit. For
example:

{% include figure
image_path="/assets/images/inotify-limit.png"
alt="Inotify limit error message"
%}

## Increase the inotify limits

To increase the inotify limits permanently (across reboots), you can set the values in
the configuration file `/etc/sysctl.conf`. For instance, adding the following lines to
set the maximum number of watches to 524,288 and the maximum number of instances to
1024:

```
fs.inotify.max_user_watches=524288
fs.inotify.max_user_instances=1024
```

Then reload the configuration with `sudo sysctl -d` for the changes to take effect. You
will also need pause and resume syncing for any new limits to apply to Maestral.

You can also set the limits temporarily, until the next reboot, with `sysctl`:

```shell
$ sudo sysctl fs.inotify.max_user_watches=524288
```

Be sure to choose limits that are higher than the total number watches created by *all*
processes.

## How many inotify watches and instances do I need?

If you are curious to see how many inotify watches are currently running, you can filter
the ouput of `lsof` for inotify watches:

```shell
$ lsof | grep inotify | wc -l
```

Likewise, if you'd like to know which programs have created an inotify instance, you can
run:

```shell
for foo in /proc/*/fd/*; do readlink -f $foo; done | grep inotify | sort | uniq -c | sort -nr
```

This will get all inotify instances for each process, count the number of instances for
each process and display the output hierarchically with processes that use the most
instances coming first. An example output could look like this:

```shell
4 /proc/9370/fd/anon_inode:inotify
2 /proc/2942/fd/anon_inode:inotify
1 /proc/2570/fd/anon_inode:inotify
1 /proc/2524/fd/anon_inode:inotify
```

The above output tells us that the process with PID 9370 is consuming 4 inotify instances.
