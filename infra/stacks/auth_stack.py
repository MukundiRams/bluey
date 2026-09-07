from aws_cdk import Stack, aws_cognito as cognito
from constructs import Construct


class BlueyAuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.user_pool = cognito.UserPool(
            self,
            "BlueyUserPool",
            user_pool_name="bluey-user-pool",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
                fullname=cognito.StandardAttribute(required=True, mutable=True),
            ),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            mfa=cognito.Mfa.OFF,
        )

        self.bankers_group = cognito.CfnUserPoolGroup(
            self,
            "BankersGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="Bankers",
        )

        self.spa_client = self.user_pool.add_client(
            "BlueyWebClientPublic",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_password=True),
        )
