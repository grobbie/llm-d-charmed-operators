variable "app_name" {
  type        = string
  description = "Name of the deployed application"
  default     = "llm-d-inference-scheduler-k8s"
}

variable "channel" {
  type        = string
  description = "Charmhub channel to deploy the charm from"
  default     = "edge"
}

variable "constraints" {
  type        = string
  description = "Constraints to be used when deploying this application"
  default     = "cores=2 mem=4G"
}

variable "config" {
  type        = map(string)
  description = "Configuration to deploy this application with"
  default     = {}
}

variable "model_name" {
  type        = string
  description = "Name of Juju model where the application is to be deployed"
}

variable "revision" {
  type        = number
  description = "Revision of the charm to deploy"
  default     = null
}

variable "units" {
  type        = number
  description = "Number of units to deploy with this name and configuration"
  default     = 1
}
