---
title: maestral completion
permalink: /cli/completion
---

Generate completion script for your shell.

This command can generate shell completion scripts for bash, zsh or fish. Follow the
instructions below for your shell to load the resulting script. The exact config file
locations might vary based on your system. Make sure to restart your shell before
testing whether completions are working.

#### bash

You can enable shell completion for all users by generating and saving the script as
follows:

```shell
$ maestral completion bash > /usr/share/bash-completion/completions/maestral
```

To enable shell completion for the current user only, save the script in a location of
your choice, for example `~/.local/completions/maestral`, and source it in `~/.bashrc`
by adding the line:

```shell
. ~/.local/completions/maestral
```

#### zsh

Generate a `_maestral` completion script and put it somewhere in your `$fpath`. For
example:

```shell
$ maestral completion zsh > /usr/local/share/zsh/site-functions/_maestral
```

You can also save the completion script in a location of your choice and source it in
`~/.zshrc`. Ensure that the following is present in your `~/.zshrc`:

```shell
autoload -Uz compinit && compinit
```

#### fish

Generate and save a `maestral.fish` completion script as follows. For all users:

```shell
$ maestral completion fish > /usr/share/fish/vendor_completions.d/maestral.fish
```

For the current user only:

```shell
$ maestral completion fish > ~/.config/fish/completions/maestral.fish
```

### Syntax

```shell
maestral completion [OPTIONS] {bash|zsh|fish}
```

### Options

```
--help                    Show help for this command and exit.
```