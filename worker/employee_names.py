"""Generate matching employee names and mailbox local parts without external calls."""
import secrets


FIRST_NAMES = tuple('Alice Benjamin Clara Dominic Elena Felix Georgia Henry Iris Julian '
                    'Katherine Leo Maya Nolan Oscar Phoebe Quentin Rose Simon Tessa '
                    'Arthur Beatrice Colin Diana Elliot Fiona Gabriel Hazel Isaac Julia '
                    'Laura Marcus Naomi Oliver Peter Rachel Sebastian Violet Wesley Zoe'.split())
LAST_NAMES = tuple('Adams Bennett Clarke Dawson Ellis Foster Grant Hayes Irving Jenkins '
                   'Knight Lewis Mitchell Nelson Owens Palmer Quinn Roberts Spencer Turner '
                   'Underwood Vincent Walsh Young Abbott Blake Cooper Dixon Edwards Fisher '
                   'Graham Harrison Lawson Marshall Newton Porter Russell Sullivan Wallace Brooks'.split())


def employee_identity(domain, occupied):
    """Reserve one lowercase email in the caller's batch set; never return a collision."""
    for _ in range(80):
        first, last = secrets.choice(FIRST_NAMES), secrets.choice(LAST_NAMES)
        email = f'{first}{last}{secrets.randbelow(10000):04d}@{domain}'.lower()
        if email not in occupied:
            occupied.add(email)
            return email, first, last
    raise ValueError('无法生成不重复的员工邮箱，请重新预览。')
