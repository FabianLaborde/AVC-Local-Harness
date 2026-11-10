"""Generation schemas for local Ollama, followed by semantic Python validation.

JSON mode alone guarantees syntax, not the inter-stage contract.
https://docs.ollama.com/capabilities/structured-outputs
"""


def obj(properties):
    return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)


def text(limit=800):
    return dict(type='string',maxLength=limit)


def array(items,maximum,minimum=0):
    return dict(type='array',items=items,minItems=minimum,maxItems=maximum)


def choice(values):
    return dict(type='string',enum=list(values))


AREA=dict(anyOf=[dict(type='null'),array(dict(type='number',minimum=0,maximum=1),4,4)])
SCENE=obj(dict(
    canvas=obj(dict(bounds=AREA,background=text())),
    nodes=array(obj(dict(id=text(60),parent=dict(anyOf=[dict(type='null'),text(60)]),
        kind=choice(['container','text','input','password','button','icon','toggle','link','separator','other']),
        label=text(300),bounds=AREA,fill=text(300),foreground=text(300),shape=text(200),detail=text(500))),60,1),
    rows=array(array(text(60),20,1),20),uncertainties=array(text(300),12)))


def contract_schema(node_ids):
    return obj(dict(scope=choice(['partial']),goals=array(obj(dict(
        id=text(60),targets=array(choice(node_ids),60),basis=choice(['request','reference']),expected=text(900))),8,1),
        preserve=array(text(500),12)))


def review_schema(goal_ids):
    return obj(dict(checks=array(obj(dict(goal_id=choice(goal_ids),
        state=choice(['observed','different','uncertain']),confidence=choice(['high','medium','low']),
        evidence=text(1000),suggestion=text(700),actual_bounds=AREA)),len(goal_ids),len(goal_ids))))


CONFIRM=obj(dict(confirmed=dict(type='boolean'),evidence=text(1000)))
INTENT=obj(dict(scope=choice(['reproduce','partial']),preserve=array(text(500),12)))
