#!/usr/bin/python2
from dulwich.repo import Repo
import crecord

class Ui:
    def warn(self, message):
        print message

    def username(self):
        return ""

repo = Repo(".")
ui = Ui()
crecord.crecord(ui, repo, user="")
