# -*- coding: utf-8 -*-
"""健康提醒裁切修复验证：启动后立即触发强提醒，看角色是否完整显示。"""
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

pet.bubble.show_text("🔔 2 秒后触发健康提醒，注意看角色是否被裁切", 3000)

def trigger():
    pet._start_health_alert("sit")

QTimer.singleShot(2000, trigger)

print("启动：2 秒后触发久坐强提醒（放大1.8倍）")
print(">>> 角色应完整显示，四周不被裁切")
print(">>> 右键可解除提醒看恢复效果")
sys.exit(app.exec_())
