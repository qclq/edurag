PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 18


def password_length_is_valid(password: str) -> bool:
    return PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH
