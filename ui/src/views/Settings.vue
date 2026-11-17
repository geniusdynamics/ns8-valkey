<!--
  Copyright (C) 2022 Nethesis S.r.l.
  Copyright (C) 2026 Genius Dynamics
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("settings.title") }}</h2>
      </cv-column>
    </cv-row>
    <cv-row v-if="error.getConfiguration">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.get-configuration')"
          :description="error.getConfiguration"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column>
        <cv-tile light>
          <cv-skeleton-text
            v-if="loading.getConfiguration"
            :paragraph="true"
            :line-count="8"
          ></cv-skeleton-text>
          <cv-form v-else @submit.prevent="configureModule">
            <cv-radio-group
              :legend="$t('settings.role')"
              vertical
              class="mg-bottom"
              ref="role"
            >
              <cv-radio-button
                :label="$t('settings.role_standalone')"
                value="standalone"
                v-model="role"
                :disabled="isFormDisabled || !canChooseRole('standalone')"
              />
              <p class="radio-description">
                {{ $t("settings.role_standalone_description") }}
              </p>
              <cv-radio-button
                :label="$t('settings.role_initial')"
                value="initial"
                v-model="role"
                :disabled="isFormDisabled || !canChooseRole('initial')"
              />
              <p class="radio-description">
                {{ $t("settings.role_initial_description") }}
              </p>
              <cv-radio-button
                :label="$t('settings.role_join')"
                value="join"
                v-model="role"
                :disabled="isFormDisabled || !canChooseRole('join')"
              />
              <p class="radio-description">
                {{ $t("settings.role_join_description") }}
              </p>
            </cv-radio-group>
            <div v-if="role === 'join'" class="maxwidth mg-bottom">
              <NsComboBox
                v-model="clusterId"
                :options="candidateOptions"
                :title="$t('settings.cluster_id')"
                :label="$t('settings.cluster_id_placeholder')"
                :invalid-message="error.cluster_id"
                :disabled="isFormDisabled || savedRole === 'join'"
                :auto-filter="true"
                :auto-highlight="true"
                class="mg-bottom"
                ref="cluster_id"
              />
              <NsInlineNotification
                v-if="!candidateOptions.length && savedRole !== 'join'"
                kind="info"
                :title="$t('settings.no_clusters')"
                :description="$t('settings.no_clusters_description')"
                :showCloseButton="false"
              />
            </div>
            <NsInlineNotification
              v-if="role !== 'standalone' && expectedMembers < 3"
              kind="warning"
              :title="$t('settings.quorum_warning')"
              :description="
                $t('settings.quorum_warning_description', {
                  count: expectedMembers,
                })
              "
              :showCloseButton="false"
            />
            <cv-radio-group
              v-if="role !== 'standalone'"
              :legend="$t('settings.consistency')"
              vertical
              class="mg-bottom"
              ref="consistency"
            >
              <cv-radio-button
                :label="$t('settings.consistency_availability')"
                value="availability"
                v-model="consistency"
                :disabled="isFormDisabled || isConsistencyLocked"
              />
              <p class="radio-description">
                {{ $t("settings.consistency_availability_description") }}
              </p>
              <cv-radio-button
                :label="$t('settings.consistency_consistency')"
                value="consistency"
                v-model="consistency"
                :disabled="isFormDisabled || isConsistencyLocked"
              />
              <p class="radio-description">
                {{ $t("settings.consistency_consistency_description") }}
              </p>
            </cv-radio-group>
            <NsInlineNotification
              v-if="
                role !== 'standalone' &&
                consistency === 'consistency' &&
                expectedMembers < 2
              "
              kind="warning"
              :title="$t('settings.consistency_warning')"
              :description="$t('settings.consistency_warning_description')"
              :showCloseButton="false"
            />
            <NsInlineNotification
              v-if="savedRole === 'join' && role === 'standalone'"
              kind="warning"
              :title="$t('settings.leave_warning')"
              :description="$t('settings.leave_warning_description')"
              :showCloseButton="false"
            />
            <!-- advanced options -->
            <cv-accordion ref="accordion" class="maxwidth mg-bottom">
              <cv-accordion-item :open="toggleAccordion[0]">
                <template slot="title">{{ $t("settings.advanced") }}</template>
                <template slot="content">
                  <NsTextInput
                    v-model.trim="maxmemoryMb"
                    type="number"
                    min="0"
                    :label="$t('settings.maxmemory_mb')"
                    :helper-text="$t('settings.maxmemory_mb_helper')"
                    :invalid-message="$t(error.maxmemory_mb)"
                    :disabled="isFormDisabled"
                    ref="maxmemory_mb"
                  />
                </template>
              </cv-accordion-item>
            </cv-accordion>
            <NsInlineNotification
              v-if="error.configureModule"
              kind="error"
              :title="$t('action.configure-module')"
              :description="error.configureModule"
              :showCloseButton="false"
            />
            <NsInlineNotification
              v-if="error.role"
              kind="error"
              :title="$t('settings.role')"
              :description="error.role"
              :showCloseButton="false"
            />
            <NsButton
              kind="primary"
              :icon="Save20"
              :loading="loading.configureModule"
              :disabled="isFormDisabled"
              >{{ $t("settings.save") }}</NsButton
            >
          </cv-form>
        </cv-tile>
      </cv-column>
    </cv-row>
  </cv-grid>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import {
  QueryParamService,
  UtilService,
  TaskService,
  IconService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";

export default {
  name: "Settings",
  mixins: [
    TaskService,
    IconService,
    UtilService,
    QueryParamService,
    PageTitleService,
  ],
  pageTitle() {
    return this.$t("settings.title") + " - " + this.appName;
  },
  data() {
    return {
      q: {
        page: "settings",
      },
      urlCheckInterval: null,
      role: "standalone",
      savedRole: "",
      clusterId: "",
      maxmemoryMb: "0",
      consistency: "availability",
      members: [],
      candidates: [],
      loading: {
        getConfiguration: false,
        configureModule: false,
      },
      error: {
        getConfiguration: "",
        configureModule: "",
        role: "",
        cluster_id: "",
        maxmemory_mb: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    isConsistencyLocked() {
      // A joining member takes the setting of its cluster
      return this.role === "join" && this.savedRole !== "join";
    },
    isFormDisabled() {
      return this.loading.getConfiguration || this.loading.configureModule;
    },
    candidateOptions() {
      return this.candidates.map((c) => ({
        name: c.cluster_id,
        value: c.cluster_id,
        label: this.$t("settings.cluster_option", {
          cluster: c.cluster_id,
          members: c.members.join(", "),
        }),
      }));
    },
    expectedMembers() {
      if (this.role === "initial") {
        return Math.max(this.members.length, 1);
      }
      if (this.role === "join") {
        if (this.savedRole === "join") {
          return this.members.length;
        }
        const cluster = this.candidates.find(
          (c) => c.cluster_id === this.clusterId
        );
        return cluster ? cluster.members.length + 1 : 1;
      }
      return 1;
    },
  },
  created() {
    this.getConfiguration();
  },
  beforeRouteEnter(to, from, next) {
    next((vm) => {
      vm.watchQueryData(vm);
      vm.urlCheckInterval = vm.initUrlBindingForApp(vm, vm.q.page);
    });
  },
  beforeRouteLeave(to, from, next) {
    clearInterval(this.urlCheckInterval);
    next();
  },
  methods: {
    canChooseRole(role) {
      // Allowed changes, as checked by configure-module
      const saved = this.savedRole;
      if (!saved || saved === "standalone" || saved === role) {
        return true;
      }
      if (role === "standalone") {
        return saved === "join" || this.members.length <= 1;
      }
      return false;
    },
    async getConfiguration() {
      this.loading.getConfiguration = true;
      this.error.getConfiguration = "";
      const taskAction = "get-configuration";
      const eventId = this.getUuid();
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getConfigurationAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getConfigurationCompleted
      );
      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: taskAction,
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.getConfiguration = this.getErrorMessage(err);
        this.loading.getConfiguration = false;
      }
    },
    getConfigurationAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getConfiguration = this.$t("error.generic_error");
      this.loading.getConfiguration = false;
    },
    getConfigurationCompleted(taskContext, taskResult) {
      const config = taskResult.output;
      this.savedRole = config.role;
      this.role = config.role || "standalone";
      this.clusterId = config.cluster_id;
      this.maxmemoryMb = String(config.maxmemory_mb || 0);
      this.consistency = config.consistency || "availability";
      this.members = config.members || [];
      this.candidates = config.cluster_candidates || [];
      this.loading.getConfiguration = false;
    },
    validateConfigureModule() {
      this.clearErrors(this);
      let isValidationOk = true;
      if (this.role === "join" && !this.clusterId) {
        this.error.cluster_id = this.$t("common.required");
        this.focusElement("cluster_id");
        isValidationOk = false;
      }
      const mem = Number(this.maxmemoryMb || 0);
      if (!Number.isInteger(mem) || mem < 0) {
        this.error.maxmemory_mb = "settings.maxmemory_mb_invalid";
        this.focusElement("maxmemory_mb");
        isValidationOk = false;
      }
      return isValidationOk;
    },
    configureModuleValidationFailed(validationErrors) {
      this.loading.configureModule = false;
      for (const validationError of validationErrors) {
        const param = validationError.parameter;
        const message = this.$t("settings." + validationError.error);
        if (param in this.error) {
          this.error[param] = message;
        } else {
          this.error.configureModule = message;
        }
      }
    },
    async configureModule() {
      if (!this.validateConfigureModule()) {
        return;
      }
      this.loading.configureModule = true;
      const taskAction = "configure-module";
      const eventId = this.getUuid();
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.configureModuleAborted
      );
      this.core.$root.$once(
        `${taskAction}-validation-failed-${eventId}`,
        this.configureModuleValidationFailed
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.configureModuleCompleted
      );
      const data = {
        role: this.role,
        maxmemory_mb: Number(this.maxmemoryMb || 0),
      };
      if (this.role === "join") {
        data.cluster_id = this.clusterId;
      }
      if (this.role !== "standalone" && !this.isConsistencyLocked) {
        data.consistency = this.consistency;
      }
      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: taskAction,
          data,
          extra: {
            title: this.$t("settings.instance_configuration", {
              instance: this.instanceName,
            }),
            description: this.$t("settings.configuring"),
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.configureModule = this.getErrorMessage(err);
        this.loading.configureModule = false;
      }
    },
    configureModuleAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.configureModule = this.$t("error.generic_error");
      this.loading.configureModule = false;
    },
    configureModuleCompleted() {
      this.loading.configureModule = false;
      this.getConfiguration();
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";
.mg-bottom {
  margin-bottom: $spacing-06;
}

.maxwidth {
  max-width: 38rem;
}

.radio-description {
  max-width: 38rem;
  margin: 0 0 $spacing-05 $spacing-07;
  color: $text-02;
}
</style>
