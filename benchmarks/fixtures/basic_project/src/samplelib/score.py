def classify_score(value):
    if value < 0:
        return "negative"
    if value == 0:
        return "zero"
    return "positive"
