import re

STOPWORDS = {"what","is","the","are","a","an","of","to","in","on","for","and","or","do","does",
             "can","you","me","tell","about","this","that","how","many","which","who","when",
             "where","why","with","from","be","it","its","as","by","at","please"}


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 2}