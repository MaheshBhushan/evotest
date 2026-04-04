class Greeter:
    def __init__(self):
        self.prefix = "hi"

    def greet(self, name):
        return f"{self.prefix} {name}"
