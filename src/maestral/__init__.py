import warnings


__version__ = "1.8.0.dev0"
__author__ = "Sam Schott"
__url__ = "https://maestral.app"


# suppress Python 3.9 warning from rubicon-objc
warnings.filterwarnings("ignore", module="rubicon", category=UserWarning)
