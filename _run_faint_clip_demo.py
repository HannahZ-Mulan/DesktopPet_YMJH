# -*- coding: utf-8 -*-
"""血量归零昏迷裁切验证：启动后立即昏迷，看角色半透明时是否完整显示。"""
import sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.argv = ["desktop_pet.py"]
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
import importlib.util
spec = importlib.util.spec_from_file_location("dpet", "desktop_pet.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

pet = mod.PetWindow()
pet.show()
app.processEvents()
pet._inventory["shenshou_dan"] = 5

pet.bubble.show_text("💔 2 秒后血量归零进入昏迷，注意看角色是否被裁切", 3000)

def faint():
    pet._hp = 0.0
    pet._enter_faint()

QTimer.singleShot(2000, faint)

print("启动：2 秒后昏迷（半透明），看角色是否完整")
print(">>> 右键喂神授丹可苏醒")
sys.exit(app.exec_())
