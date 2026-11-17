<!--
  Copyright (C) 2023 Nethesis S.r.l.
  Copyright (C) 2026 Genius Dynamics
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("status.title") }}</h2>
      </cv-column>
    </cv-row>
    <cv-row v-if="error.getStatus">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.get-status')"
          :description="error.getStatus"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>
    <cv-row v-if="error.listBackupRepositories">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.list-backup-repositories')"
          :description="error.listBackupRepositories"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>
    <cv-row v-if="error.listBackups">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.list-backups')"
          :description="error.listBackups"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="valkeyTitle"
          :description="valkeyDescription"
          :icon="Settings32"
          :loading="loading.getClusterStatus"
          :isErrorShown="!!error.getClusterStatus"
          :errorTitle="$t('action.get-cluster-status')"
          :errorDescription="error.getClusterStatus"
          class="min-height-card"
        >
          <template slot="content">
            <NsButton
              v-if="!cluster.configured && !loading.getClusterStatus"
              kind="ghost"
              :icon="ArrowRight20"
              @click="goToAppPage(instanceName, 'settings')"
            >
              {{ $t("status.configure") }}
            </NsButton>
          </template>
        </NsInfoCard>
      </cv-column>
      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="status.instance || '-'"
          :description="$t('status.app_instance')"
          :icon="Application32"
          :loading="loading.getStatus"
          class="min-height-card"
        />
      </cv-column>
      <cv-column :md="4" :max="4">
        <NsInfoCard
          light
          :title="installationNodeTitle"
          :titleTooltip="installationNodeTitleTooltip"
          :description="$t('status.installation_node')"
          :icon="Chip32"
          :loading="loading.getStatus"
          class="min-height-card"
        />
      </cv-column>
      <cv-column :md="4" :max="4">
        <NsBackupCard
          :title="core.$t('backup.title')"
          :noBackupMessage="core.$t('backup.no_backup_configured')"
          :goToBackupLabel="core.$t('backup.go_to_backup')"
          :repositoryLabel="core.$t('backup.repository')"
          :statusLabel="core.$t('common.status')"
          :statusSuccessLabel="core.$t('common.success')"
          :statusNotRunLabel="core.$t('backup.backup_has_not_run_yet')"
          :statusErrorLabel="core.$t('error.error')"
          :completedLabel="core.$t('backup.completed')"
          :durationLabel="core.$t('backup.duration')"
          :totalSizeLabel="core.$t('backup.total_size')"
          :totalFileCountLabel="core.$t('backup.total_file_count')"
          :backupDisabledLabel="core.$t('common.disabled')"
          :showMoreLabel="core.$t('common.show_more')"
          :moduleId="instanceName"
          :moduleUiName="instanceLabel"
          :repositories="backupRepositories"
          :backups="backups"
          :loading="loading.listBackupRepositories || loading.listBackups"
          :coreContext="core"
          light
        />
      </cv-column>
      <cv-column :md="4" :max="4">
        <NsSystemLogsCard
          :title="core.$t('system_logs.card_title')"
          :description="
            core.$t('system_logs.card_description', {
              name: instanceLabel || instanceName,
            })
          "
          :buttonLabel="core.$t('system_logs.card_button_label')"
          :router="core.$router"
          context="module"
          :moduleId="instanceName"
          light
        />
      </cv-column>
    </cv-row>
    <!-- cluster -->
    <template v-if="cluster.configured && cluster.ha_mode === 'cluster'">
      <cv-row>
        <cv-column class="page-subtitle">
          <h4>
            {{ $t("status.cluster_title", { cluster: cluster.cluster_id }) }}
          </h4>
        </cv-column>
      </cv-row>
      <cv-row v-for="warning in cluster.warnings" :key="warning">
        <cv-column>
          <NsInlineNotification
            :kind="warning === 'below_quorum' ? 'warning' : 'error'"
            :title="$t('status.warning_' + warning)"
            :description="
              $t('status.warning_' + warning + '_description', {
                count: cluster.members_count,
              })
            "
            :showCloseButton="false"
          />
        </cv-column>
      </cv-row>
      <cv-row>
        <cv-column :md="4" :max="4">
          <NsInfoCard
            light
            :title="String(cluster.members_count)"
            :description="$t('status.members')"
            :icon="Chip32"
            class="min-height-card"
          />
        </cv-column>
        <cv-column :md="4" :max="4">
          <NsInfoCard
            light
            :title="cluster.quorum_ok ? $t('status.yes') : $t('status.no')"
            :description="$t('status.quorum')"
            :icon="Cube32"
            class="min-height-card"
          />
        </cv-column>
        <cv-column :md="4" :max="4">
          <NsInfoCard
            light
            :title="
              $tc('status.tolerated_failures', cluster.failure_tolerance, {
                count: cluster.failure_tolerance,
              })
            "
            :description="
              $t('status.cluster_state', { state: cluster.cluster_state })
            "
            :icon="Application32"
            class="min-height-card"
          />
        </cv-column>
      </cv-row>
      <cv-row>
        <cv-column>
          <cv-tile light>
            <cv-structured-list>
              <template slot="headings">
                <cv-structured-list-heading>{{
                  $t("status.member")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.process")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.endpoint")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.role")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.slots")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.replication")
                }}</cv-structured-list-heading>
              </template>
              <template slot="items">
                <cv-structured-list-item
                  v-for="proc in cluster.processes"
                  :key="proc.endpoint"
                >
                  <cv-structured-list-data
                    >{{ proc.module_id }} ({{ $t("status.node") }}
                    {{ proc.node }})</cv-structured-list-data
                  >
                  <cv-structured-list-data>{{
                    $t("status.process_" + proc.process)
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>{{
                    proc.endpoint
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>
                    <NsTag
                      v-if="!proc.reachable"
                      kind="red"
                      :label="$t('status.down')"
                    />
                    <span v-else>{{ $t("status.role_" + proc.role) }}</span>
                  </cv-structured-list-data>
                  <cv-structured-list-data>{{
                    proc.slots
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>
                    <span v-if="proc.role === 'replica'">
                      {{ proc.primary }} · {{ proc.link || "-" }}
                      <span v-if="proc.lag_bytes !== null">
                        · {{ proc.lag_bytes | byteFormat }}
                      </span>
                    </span>
                    <span v-else>-</span>
                  </cv-structured-list-data>
                </cv-structured-list-item>
              </template>
            </cv-structured-list>
          </cv-tile>
        </cv-column>
      </cv-row>
    </template>
    <!-- clients -->
    <template v-if="cluster.configured">
      <cv-row>
        <cv-column class="page-subtitle">
          <h4>{{ $t("status.clients") }}</h4>
        </cv-column>
      </cv-row>
      <cv-row>
        <cv-column>
          <cv-tile light>
            <NsEmptyState
              v-if="!clients.length"
              :title="$t('status.no_clients')"
            >
              <template #description>{{
                $t("status.no_clients_description")
              }}</template>
            </NsEmptyState>
            <cv-structured-list v-else>
              <template slot="headings">
                <cv-structured-list-heading>{{
                  $t("status.client")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.username")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.key_prefix")
                }}</cv-structured-list-heading>
              </template>
              <template slot="items">
                <cv-structured-list-item
                  v-for="client in clients"
                  :key="client.username"
                >
                  <cv-structured-list-data>{{
                    client.client_id
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>{{
                    client.username
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>{{
                    client.key_prefix || $t("status.all_keys")
                  }}</cv-structured-list-data>
                </cv-structured-list-item>
              </template>
            </cv-structured-list>
          </cv-tile>
        </cv-column>
      </cv-row>
    </template>
    <!-- services -->
    <cv-row>
      <cv-column class="page-subtitle">
        <h4>{{ $tc("status.services", 2) }}</h4>
      </cv-column>
    </cv-row>
    <cv-row v-if="!loading.getStatus">
      <cv-column v-if="!status.services.length">
        <cv-tile light>
          <NsEmptyState :title="$t('status.no_services')"> </NsEmptyState>
        </cv-tile>
      </cv-column>
      <cv-column
        v-else
        v-for="(service, index) in status.services"
        :key="index"
        :md="4"
        :max="4"
      >
        <NsSystemdServiceCard
          light
          class="min-height-card"
          :serviceName="service.name"
          :active="service.active"
          :failed="service.failed"
          :enabled="service.enabled"
          :icon="Cube32"
        />
      </cv-column>
    </cv-row>
    <cv-row v-else>
      <cv-column :md="4" :max="4">
        <cv-tile light>
          <cv-skeleton-text
            :paragraph="true"
            :line-count="4"
          ></cv-skeleton-text>
        </cv-tile>
      </cv-column>
    </cv-row>
    <!-- images -->
    <cv-row>
      <cv-column class="page-subtitle">
        <h4>{{ $tc("status.app_images", 2) }}</h4>
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column>
        <cv-tile light>
          <div v-if="!loading.getStatus">
            <NsEmptyState
              v-if="!status.images.length"
              :title="$t('status.no_images')"
            >
            </NsEmptyState>
            <cv-structured-list v-else>
              <template slot="headings">
                <cv-structured-list-heading>{{
                  $t("status.name")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.size")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.created")
                }}</cv-structured-list-heading>
              </template>
              <template slot="items">
                <cv-structured-list-item
                  v-for="(image, index) in status.images"
                  :key="index"
                >
                  <cv-structured-list-data class="break-word">{{
                    image.name
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>{{
                    image.size
                  }}</cv-structured-list-data>
                  <cv-structured-list-data class="break-word">{{
                    image.created
                  }}</cv-structured-list-data>
                </cv-structured-list-item>
              </template>
            </cv-structured-list>
          </div>
          <cv-skeleton-text
            v-else
            :paragraph="true"
            :line-count="5"
          ></cv-skeleton-text>
        </cv-tile>
      </cv-column>
    </cv-row>
    <!-- volumes -->
    <cv-row>
      <cv-column class="page-subtitle">
        <h4>{{ $tc("status.app_volumes", 2) }}</h4>
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column>
        <cv-tile light>
          <div v-if="!loading.getStatus">
            <NsEmptyState
              v-if="!status.volumes.length"
              :title="$t('status.no_volumes')"
            >
            </NsEmptyState>
            <cv-structured-list v-else>
              <template slot="headings">
                <cv-structured-list-heading>{{
                  $t("status.name")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.mount")
                }}</cv-structured-list-heading>
                <cv-structured-list-heading>{{
                  $t("status.created")
                }}</cv-structured-list-heading>
              </template>
              <template slot="items">
                <cv-structured-list-item
                  v-for="(volume, index) in status.volumes"
                  :key="index"
                >
                  <cv-structured-list-data>{{
                    volume.name
                  }}</cv-structured-list-data>
                  <cv-structured-list-data class="break-word">{{
                    volume.mount
                  }}</cv-structured-list-data>
                  <cv-structured-list-data>{{
                    volume.created
                  }}</cv-structured-list-data>
                </cv-structured-list-item>
              </template>
            </cv-structured-list>
          </div>
          <cv-skeleton-text
            v-else
            :paragraph="true"
            :line-count="5"
          ></cv-skeleton-text>
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
  TaskService,
  IconService,
  UtilService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";

export default {
  name: "Status",
  mixins: [
    TaskService,
    QueryParamService,
    IconService,
    UtilService,
    PageTitleService,
  ],
  pageTitle() {
    return this.$t("status.title") + " - " + this.appName;
  },
  data() {
    return {
      q: {
        page: "status",
      },
      urlCheckInterval: null,
      isRedirectChecked: false,
      redirectTimeout: 0,
      cluster: {
        configured: false,
        role: "",
        ha_mode: "",
        cluster_id: "",
        endpoint: "",
        members_count: 0,
        quorum_ok: false,
        failure_tolerance: 0,
        cluster_state: "",
        processes: [],
        warnings: [],
      },
      clients: [],
      status: {
        instance: "",
        services: [],
        images: [],
        volumes: [],
      },
      backupRepositories: [],
      backups: [],
      loading: {
        getStatus: false,
        listBackupRepositories: false,
        listBackups: false,
        getClusterStatus: false,
        listClients: false,
      },
      error: {
        getClusterStatus: "",
        listClients: "",
        getStatus: "",
        listBackupRepositories: "",
        listBackups: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "instanceLabel", "core", "appName"]),
    valkeyTitle() {
      if (!this.cluster.configured) {
        return this.$t("status.not_configured");
      }
      return this.$t("status.role_title_" + this.cluster.role);
    },
    valkeyDescription() {
      if (!this.cluster.configured) {
        return this.$t("status.valkey");
      }
      return this.cluster.endpoint;
    },
    installationNodeTitle() {
      if (this.status && this.status.node) {
        if (this.status.node_ui_name) {
          return this.status.node_ui_name;
        } else {
          return this.$t("status.node") + " " + this.status.node;
        }
      } else {
        return "-";
      }
    },
    installationNodeTitleTooltip() {
      if (this.status && this.status.node_ui_name) {
        return this.$t("status.node") + " " + this.status.node;
      } else {
        return "";
      }
    },
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
  mounted() {
    this.redirectTimeout = setTimeout(
      () => (this.isRedirectChecked = true),
      200
    );
  },
  beforeUnmount() {
    clearTimeout(this.redirectTimeout);
  },
  created() {
    this.getClusterStatus();
    this.listClients();
    this.getStatus();
    this.listBackupRepositories();
  },
  methods: {
    async runReadTask(taskAction, loadingKey, onCompleted) {
      this.loading[loadingKey] = true;
      this.error[loadingKey] = "";
      const eventId = this.getUuid();
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        (taskResult, taskContext) => {
          console.error(`${taskContext.action} aborted`, taskResult);
          this.error[loadingKey] = this.$t("error.generic_error");
          this.loading[loadingKey] = false;
        }
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        (taskContext, taskResult) => {
          onCompleted(taskResult.output);
          this.loading[loadingKey] = false;
        }
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
        this.error[loadingKey] = this.getErrorMessage(err);
        this.loading[loadingKey] = false;
      }
    },
    getClusterStatus() {
      this.runReadTask("get-cluster-status", "getClusterStatus", (output) => {
        this.cluster = output;
      });
    },
    listClients() {
      this.runReadTask("list-clients", "listClients", (output) => {
        this.clients = output.clients;
      });
    },
    async getStatus() {
      this.loading.getStatus = true;
      this.error.getStatus = "";
      const taskAction = "get-status";
      const eventId = this.getUuid();

      // register to task error
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getStatusAborted
      );

      // register to task completion
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getStatusCompleted
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
        this.error.getStatus = this.getErrorMessage(err);
        this.loading.getStatus = false;
        return;
      }
    },
    getStatusAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getStatus = this.$t("error.generic_error");
      this.loading.getStatus = false;
    },
    getStatusCompleted(taskContext, taskResult) {
      this.status = taskResult.output;
      this.loading.getStatus = false;
    },
    async listBackupRepositories() {
      this.loading.listBackupRepositories = true;
      this.error.listBackupRepositories = "";
      const taskAction = "list-backup-repositories";
      const eventId = this.getUuid();

      // register to task error
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.listBackupRepositoriesAborted
      );

      // register to task completion
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.listBackupRepositoriesCompleted
      );

      const res = await to(
        this.createClusterTaskForApp({
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
        this.error.listBackupRepositories = this.getErrorMessage(err);
        this.loading.listBackupRepositories = false;
        return;
      }
    },
    listBackupRepositoriesAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.listBackupRepositories = this.$t("error.generic_error");
      this.loading.listBackupRepositories = false;
    },
    listBackupRepositoriesCompleted(taskContext, taskResult) {
      let backupRepositories = taskResult.output.repositories.sort(
        this.sortByProperty("name")
      );
      this.backupRepositories = backupRepositories;
      this.loading.listBackupRepositories = false;
      this.listBackups();
    },
    async listBackups() {
      this.loading.listBackups = true;
      this.error.listBackups = "";
      const taskAction = "list-backups";
      const eventId = this.getUuid();

      // register to task error
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.listBackupsAborted
      );

      // register to task completion
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.listBackupsCompleted
      );

      const res = await to(
        this.createClusterTaskForApp({
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
        this.error.listBackups = this.getErrorMessage(err);
        this.loading.listBackups = false;
        return;
      }
    },
    listBackupsAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.listBackups = this.$t("error.generic_error");
      this.loading.listBackups = false;
    },
    listBackupsCompleted(taskContext, taskResult) {
      let backups = taskResult.output.backups;
      backups.sort(this.sortByProperty("name"));

      // get repository name
      for (const backup of backups) {
        const repo = this.backupRepositories.find(
          (r) => r.id == backup.repository
        );

        if (repo) {
          backup.repoName = repo.name;
        }
      }
      this.backups = backups;
      this.loading.listBackups = false;
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";

.break-word {
  word-wrap: break-word;
  max-width: 30vw;
}
</style>
