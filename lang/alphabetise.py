from en_US import STRINGS

keys = [k.split(".")[0] for k, v in STRINGS.items()]
keys = list(set(keys))
keys = sorted(keys)

for key in keys:
    items = [[k, v] for k, v in STRINGS.items() if k.split(".")[0] == key]
    items = sorted(items, key=lambda x: x[0])
    for key, value in items:
        if "\n" in value:
            print(f"\"{key}\": \"\"\"{value}\"\"\",")
        else:
            print(f"\"{key}\": \"{value}\",")
    print("\n")
