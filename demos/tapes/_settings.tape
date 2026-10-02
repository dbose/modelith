# Shared VHS settings for every Modelith demo — Source this at the top of each tape so the
# whole set is visually consistent (one font, theme, size, cadence). Tune here once.
#
# Why these values: 1280x900 is tall enough that a multi-command demo does not scroll the
# earlier commands off the top; 22px font stays legible when embedded small; the amber theme
# echoes the canvas accent; 70ms typing reads as a calm human typing (not a race); and each
# tape adds generous Sleeps after a command so the viewer can read the output before the next.

Set Shell "zsh"
Set FontSize 22
Set FontFamily "JetBrains Mono"
Set LetterSpacing 0
Set LineHeight 1.2
Set Width 1280
Set Height 900
Set Padding 28
Set TypingSpeed 70ms
Set WaitTimeout 30s
Set Theme { "name": "Modelith", "black": "#14110f", "red": "#f0857a", "green": "#6cbf73", "yellow": "#e0a63a", "blue": "#7fb0d8", "magenta": "#d98ba8", "cyan": "#9fd0c7", "white": "#ece6dd", "brightBlack": "#4a3c2c", "brightRed": "#f0857a", "brightGreen": "#6cbf73", "brightYellow": "#f2c14e", "brightBlue": "#7fb0d8", "brightMagenta": "#d98ba8", "brightCyan": "#9fd0c7", "brightWhite": "#ffffff", "background": "#14110f", "foreground": "#ece6dd", "cursor": "#e0a63a", "selection": "#4a3c2c" }
