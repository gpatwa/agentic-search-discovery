# Backend settings: none of it is secret (it names the storage account, not its key). Auth is Entra: the caller's `az login`
# locally, OIDC in CI. CI overrides these from the repository variables TF_STATE_* (set by infra/terraform/platform-identity).
resource_group_name  = "terraform-state-rg"
storage_account_name = "ragterraformstategp"
container_name       = "tfstate"
key                  = "discovery-pilot/terraform.tfstate"
use_azuread_auth     = true
