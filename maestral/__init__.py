from .client import MaestralClient
from .main import Maestral
try:
    import pyqt5
    from .gui.main import MaestralApp
except ImportError:
    print('Warning: PyQt5 is required to run the Maestral GUI. Run `pip install pyqt5` to install it.')