class EmailService:
    def send_verification_code(self, email: str, code: str) -> None:
        raise NotImplementedError


class ConsoleEmailService(EmailService):
    def send_verification_code(self, email: str, code: str) -> None:
        # In production, replace this with a real email provider.
        print(f"[EmailService] Sending code {code} to {email}")


async def get_email_service() -> EmailService:
    return ConsoleEmailService()
