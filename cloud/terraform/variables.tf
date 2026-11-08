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
