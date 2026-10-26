# Loosely worded requests and emergencies (after)

- Loosely worded requests sorted correctly (not held): 16 of 16
- Emergencies flagged with an accepted incident type: 15 of 15
- Look-alike non-emergencies NOT flagged: 14 of 14
- Loosely worded requests that were escalated as possible emergencies: 3 of 16 (the screener is told to escalate when in doubt; a person checks every escalation)

| Group | Request | Route | Type / advice | Emergency | Pass |
|---|---|---|---|---|---|
| ambiguous | a dog wandered into my apartment in brooklyn | advice | found_stray_dog |  | yes |
| ambiguous | theres a big hole in the road on atlantic ave by my house and my tire popped, brooklyn | capital | Street Condition |  | yes |
| ambiguous | rats everywhere by the garbage cans on my block in the bronx | operating | Rodent |  | yes |
| ambiguous | the tree in front of my building on 86th st in queens looks like its about to fall over | emergency | Damaged Tree | Downed Tree | yes |
| ambiguous | landlord wont turn the heat on and its freezing in here, apt in the bronx | emergency | HEAT/HOT WATER | Other danger to life or safety | yes |
| ambiguous | someone keeps leaving their pitbull tied up outside with no water all day in queens | operating | Animal-Abuse |  | yes |
| ambiguous | people next door blasting music at 2am every single night, manhattan | operating | Noise - Residential |  | yes |
| ambiguous | theres a dead raccoon in front of my house on bay ridge parkway brooklyn | operating | Dead Animal |  | yes |
| ambiguous | loose dog running around the playground in prospect park with no owner around | emergency | Animal in a Park | Other danger to life or safety | yes |
| ambiguous | the streetlight on my corner has been out for a week, its pitch black, staten island | operating | Street Light Condition |  | yes |
| ambiguous | found a lost dog on my street in queens, it has a collar but no one around | advice | found_stray_dog |  | yes |
| ambiguous | pile of old mattresses and junk dumped on the sidewalk on fulton st brooklyn | operating | Illegal Dumping |  | yes |
| ambiguous | Hay un bache enorme en la Avenida Roosevelt en Queens, ya se me poncharon dos llantas | capital | Street Condition |  | yes |
| ambiguous | 我家楼下垃圾桶旁边到处都是老鼠，在布朗克斯 | operating | Rodent |  | yes |
| ambiguous | В моей квартире в Бруклине уже три дня нет отопления и горячей воды | operating | HEAT/HOT WATER |  | yes |
| ambiguous | 퀸즈 노던 블러바드에 가로등이 일주일째 꺼져 있어요 | operating | Street Light Condition |  | yes |
| emergency | a building collapsed in downtown manhattan | emergency | General Construction/Plumbing | Structure Collapse | yes |
| emergency | I smell gas really strong in the hallway of my building on ocean ave brooklyn | emergency | gas_smell | Utility Emergency: Gas | yes |
| emergency | smoke and flames are coming out of a window in the apartment building across the street, bronx | emergency |  | Fire | yes |
| emergency | my neighbor is stuck in the elevator between floors and nobody is answering the alarm, queens | emergency | Elevator | Elevator Incident or Emergency | yes |
| emergency | a huge tree just fell on a parked car on victory blvd staten island and theres someone inside | emergency | Damaged Tree | Downed Tree | yes |
| emergency | water is gushing out of the street and pouring into the subway entrance at 14 st and 8 ave manhattan | emergency | Water System | Flash Flood | yes |
| emergency | there was a loud explosion and windows blew out on my block in jackson heights | emergency | General Construction/Plumbing | Explosion | yes |
| emergency | my apartment is -7 degrees, the heat has been off for two days, bronx | emergency | HEAT/HOT WATER | Other danger to life or safety | yes |
| emergency | our carbon monoxide alarm keeps going off and my kids have headaches, queens | emergency | SAFETY | Other danger to life or safety | yes |
| emergency | a pit bull on my block attacked a kid and is still running loose, brooklyn | emergency | Animal-Abuse | Other danger to life or safety | yes |
| emergency | the ceiling in my kitchen is sagging and cracking and looks like it's about to come down, manhattan | emergency | PAINT/PLASTER | Structure Collapse | yes |
| emergency | Huele mucho a gas en el pasillo de mi edificio en el Bronx | emergency | gas_smell | Utility Emergency: Gas | yes |
| emergency | 对面楼里着火了，窗户在冒浓烟，在法拉盛 | emergency |  | Fire | yes |
| emergency | В Бруклине обрушилась стена дома, под завалами могут быть люди | emergency | General Construction/Plumbing | Structure Collapse | yes |
| emergency | Gen dife nan bilding vwazen mwen an nan Flatbush, gen anpil lafimen | emergency | Non-Emergency Police Matter | Fire | yes |
| not_emergency | the fire hydrant on fulton st in brooklyn has been leaking for a week | operating | Water System |  | yes |
| not_emergency | my smoke detector is broken and the landlord wont replace it, bronx apartment | operating | SAFETY |  | yes |
| not_emergency | illegal fireworks going off every night in my neighborhood in queens | operating | Illegal Fireworks |  | yes |
| not_emergency | the building next door on 5 ave manhattan has had a crack in the facade for months | operating | General Construction/Plumbing |  | yes |
| not_emergency | beach 90 street in queens floods every time it rains | capital | Sewer |  | yes |
| not_emergency | the elevator in my building on grand concourse has been out of service for 3 days | operating | Elevator |  | yes |
| not_emergency | the fire escape outside my window is blocked with furniture by a neighbor, manhattan | operating | SAFETY |  | yes |
| not_emergency | there is a gas station on my corner that leaves its lights on all night, staten island | not_city |  |  | yes |
| not_emergency | no heat in my apartment since this morning, it's about 64 inside, bronx | operating | HEAT/HOT WATER |  | yes |
| not_emergency | there is no carbon monoxide detector in my apartment and the landlord won't install one | operating | SAFETY |  | yes |
| not_emergency | a stray cat keeps sleeping on my stoop in queens | human_review |  |  | yes |
| not_emergency | La boca de incendio de mi calle en Queens lleva una semana goteando | human_review | Water System |  | yes |
| not_emergency | 每天晚上都有人在我家附近放烟花，在皇后区 | operating | Illegal Fireworks |  | yes |
| not_emergency | Mi detector de humo no funciona y el dueño no lo quiere cambiar | operating | SAFETY |  | yes |
