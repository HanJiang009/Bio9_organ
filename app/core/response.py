def ok(data=None, msg: str = ""):
    return {"code": 1, "msg": msg, "data": data}

def err(msg: str, code: int = 0, data=None):
    return {"code": code, "msg": msg, "data": data}