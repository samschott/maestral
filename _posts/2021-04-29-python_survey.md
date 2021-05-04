---
title: "Maestral's dependencies: survey"
permalink: /blog/python-survey
tags: development dependencies
excerpt: >
  Interactive command line prompts in Python: Which packages are available? How do they
  compare? How do they perform? This post explains why I choose a relatively niche
  package called "survey".
related: true
---

Maestral has a large number of dependencies, both direct and indirect. Those range from
well-known staples of the Python ecosystem such as `requests` and `sqlalchemy` to a few
lesser known packages which have not (yet) reached the popularity they deserve. This
post is the first in a series that highlights some of the latter. It starts off with
[survey](https://github.com/Exahilosys/survey), a Python package which provides
tastefully formatted, interactive command line prompts.

Maestral uses [click](https://click.palletsprojects.com), which is part of the pallets
project, to generate its command line interface (CLI). Click makes it easy to create
nested commands, generates well-formatted help pages, and supports lazy loading of
subcommands at runtime. It also provides a few utilities for interactive command line
tools such as basic prompts and progress bars.

For a sync client, it is sometimes convenient to allow for more complex inputs. The
selective sync dialog for instance provides a list of folders which checkboxes to
include or exclude them from syncing:

{% include figure
image_path="/assets/images/selective-sync.png"
image_path_dark="/assets/images/selective-sync-dark.png"
alt="Selective sync"
%}

This dialog is shown both when setting up a new account and on-demand from the
settings pane.

Such a "multiple selection" interface is more difficult to achieve in the command line.
Before version 1.3.0, we would iterate over all top level folders and prompt the user
whether to include each of them individually. This could quickly become a very tedious
task for a large number of top-level folders.

Wouldn't it be much nicer to provide a similar interface as the GUI does, with a
scrollable list of folders and checkboxes?

```
Choose which folders to include  [move: ↑↓ | pick: → | unpick: ←]
  [ ] Invoices
> [x] Refurbishing project
  [x] Summer 2018 pictures
  [x] Summer 2019 pictures
```

This is rhetoric question, of course it would! This example demonstrates that interactive
prompts are more than a fancy gimmick. They can enable entirely new types of user
interfaces in the command line and vastly simplify the user interaction.

Implementing this from scratch would be well beyond the scope of this project but it
turns out that there are already a number of Python libraries which do exactly that. I'll
give a brief overview over the main choices here and explain why I decided to use
[survey](https://github.com/Exahilosys/survey), an arguably lesser known option.

## Prompt Toolkit

[Prompt Toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) is the Python
standard for interactive command line tools. It is battle-tested, powerful, versatile
and supports a wide range of platforms. It can be used to build entire interactive
consoles and is used for instance for the well known IPython console. The authors
advertise that it is lightweight and this is certainly true for the provided feature
set.

Because Prompt Toolkit is a general purpose library, it would still require a lot of
work to write the actual prompts needed for our CLI. Furthermore, despite its small size
for the provided feature set, it does require at least 10 MB of memory at runtime.

## PyInquirer

[PyInquirer](https://github.com/CITGuru/PyInquirer) is based on Prompt Toolkit and
provides common prompts needed for Maestral out-of-the-box. It makes a few questionable
style choices by default but those are a matter of taste and can be customised. Its
major downside for adoption by Maestral is again the "large" Prompt Toolkit dependency.

## Bullet

With 0.7 MB of memory usage at runtime, [bullet](https://github.com/bchao1/bullet) is
properly lightweight for our purposes. Its claim of providing "beautiful" prompts is
questionable but this can again be customised. However, at the time of writing, it only
accepts ASCII input. This blocks its adaption by Maestral which regularly deals with
file names that contain non-ASCII characters.

## Survey

After almost giving up on the convenience of interactive dialogs, I came across
[survey](https://github.com/Exahilosys/survey). Survey is closely modelled after the
[similarly named GoLang library](https://github.com/AlecAivazis/survey) that is used for
instance in the excellent GitHub command line tool.

Here is an example of a prompt with default styling:

![Survey showcase](/assets/images/survey-showcase-2.gif)

This will look very familiar to you if you have been using Maestral's CLI, we practially
do not apply any custom styling to the excellent defaults.

Survey also qualifies as lightweight. It does import Python's relatively heavy asyncio
module. However, because Maestral imports asyncio independently anyways, the total
memory usage increases only by 0.5 MB.

Maestral uses interactive prompts for other commands beside the setup dialog. For
instance, the [`diff`]({{ site.baseurl }}/cli/diff) and [`restore`]({{ site.baseurl
}}/cli/restore) commands can both provide a list of past file versions when no version
number is given as an argument. Those lists are fetched from Dropbox servers on-demand.
The same workflow previously required two commands: First fetch all revisions numbers for
a file with the `history` command and then pass a revision number as an argument to
`diff` or `restore`. Having interactive dialogs enables us to remove the first step.

Have a look at the [documentation](https://survey.readthedocs.io) to see what is
possible with Survey and try it out if you like what you see!

The next blog post will cover Python libraries to send desktop notifications, another
rabbit-hole of popular packages which don't quite do the job...
