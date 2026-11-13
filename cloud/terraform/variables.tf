variable "project_id" {
  description = "The new Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Cloud Run and Cloud Storage region."
  type        = string
  default     = "us-central1"
}

variable "repository" {
  description = "Artifact Registry repository name."
  type        = string
  default     = "spherex-lens"
}

variable "image_tag" {
  description = "Container tag deployed to both Cloud Run resources."
  type        = string
  default     = "latest"
}

variable "deploy_runtime" {
  description = "Deploy Cloud Run, Scheduler, and public API after the image is pushed."
  type        = bool
  default     = false
}

variable "netlify_origin" {
  description = "Exact public Netlify origin, for example https://example.netlify.app."
  type        = string
  default     = ""
}

variable "live_service_url" {
  description = "URL of the deployed live-pipeline Cloud Run service, used to set data-live-api in index.html."
  type        = string
  default     = ""
}

variable "alert_email" {
  description = "Email address for uptime, refresh-failure and budget alerts. Empty = alert policies exist but notify nobody."
  type        = string
  default     = ""
}

variable "billing_account" {
  description = "Billing account ID (XXXXXX-XXXXXX-XXXXXX) for the optional budget."
  type        = string
  default     = ""
}

variable "budget_amount" {
  description = "Monthly budget in the billing account's currency; 0 disables the budget. Budgets alert, they do not cap spend."
  type        = number
  default     = 0
}

variable "votes_retention_days" {
  description = "Delete raw vote objects older than this many days; 0 keeps them forever."
  type        = number
  default     = 0
}
