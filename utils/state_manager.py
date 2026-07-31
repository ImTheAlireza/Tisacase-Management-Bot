import logging
from typing import Optional, List
from telegram.ext import ContextTypes


class StateManager:
    """
    Centralized state management utility to prevent memory pollution.
    Defines all state keys used across handlers.
    """

    # Editor workflow state keys
    EDITOR_KEYS = [
        'code',
        'product_id',
        'product_name',
        'stage',
        'mockup_files',
        'print_files',
        'file_types',
        'workspace_message_id',
        'inactivity_job',
        'editing_existing',
    ]

    # Sudo/admin workflow state keys
    SUDO_KEYS = [
        'awaiting_group_input',
        'awaiting_restore_file',
        'restore_pending',
        'selected_group_type',
        'selected_line_id',
    ]

    # Design management state keys
    DESIGN_MGMT_KEYS = [
        'awaiting_design_code',
        'awaiting_delete_line_confirm',
    ]

    # Server bill reminder state keys
    SERVER_BILL_KEYS = [
        'server_bill_reminder_active',
        'server_bill_last_reminder',
    ]

    # All workflow keys
    ALL_WORKFLOW_KEYS = EDITOR_KEYS + SUDO_KEYS + DESIGN_MGMT_KEYS + SERVER_BILL_KEYS

    @staticmethod
    def clear_editor_state(context_or_dict) -> None:
        """
        Clear editor workflow state only.

        Args:
            context_or_dict: Either ContextTypes.DEFAULT_TYPE or a dict (user_data)
        """
        user_data = context_or_dict.user_data if hasattr(context_or_dict, 'user_data') else context_or_dict
        for key in StateManager.EDITOR_KEYS:
            user_data.pop(key, None)
        logging.debug("Editor state cleared")

    @staticmethod
    def clear_sudo_state(context_or_dict) -> None:
        """
        Clear sudo workflow state only.

        Args:
            context_or_dict: Either ContextTypes.DEFAULT_TYPE or a dict (user_data)
        """
        user_data = context_or_dict.user_data if hasattr(context_or_dict, 'user_data') else context_or_dict
        for key in StateManager.SUDO_KEYS:
            user_data.pop(key, None)
        logging.debug("Sudo state cleared")

    @staticmethod
    def clear_design_mgmt_state(context_or_dict) -> None:
        """
        Clear design management state only.

        Args:
            context_or_dict: Either ContextTypes.DEFAULT_TYPE or a dict (user_data)
        """
        user_data = context_or_dict.user_data if hasattr(context_or_dict, 'user_data') else context_or_dict
        for key in StateManager.DESIGN_MGMT_KEYS:
            user_data.pop(key, None)
        logging.debug("Design management state cleared")

    @staticmethod
    def clear_server_bill_state(context_or_dict) -> None:
        """
        Clear server bill reminder state only.

        Args:
            context_or_dict: Either ContextTypes.DEFAULT_TYPE or a dict (user_data)
        """
        user_data = context_or_dict.user_data if hasattr(context_or_dict, 'user_data') else context_or_dict
        for key in StateManager.SERVER_BILL_KEYS:
            user_data.pop(key, None)
        logging.debug("Server bill state cleared")

    @staticmethod
    def clear_all_workflow_state(context_or_dict) -> None:
        """
        Clear all workflow state, preserving only db_user.

        Args:
            context_or_dict: Either ContextTypes.DEFAULT_TYPE or a dict (user_data)
        """
        user_data = context_or_dict.user_data if hasattr(context_or_dict, 'user_data') else context_or_dict
        db_user = user_data.get('db_user')
        for key in StateManager.ALL_WORKFLOW_KEYS:
            user_data.pop(key, None)
        if db_user:
            user_data['db_user'] = db_user
        logging.debug("All workflow state cleared")

    @staticmethod
    def has_active_editor_session(context_or_dict) -> bool:
        """
        Check if user has an active editor session.

        Args:
            context_or_dict: Either ContextTypes.DEFAULT_TYPE or a dict (user_data)
        """
        user_data = context_or_dict.user_data if hasattr(context_or_dict, 'user_data') else context_or_dict
        return user_data.get('code') is not None

    @staticmethod
    def has_any_active_workflow(context_or_dict) -> bool:
        """
        Check if user has any active workflow.

        Args:
            context_or_dict: Either ContextTypes.DEFAULT_TYPE or a dict (user_data)
        """
        user_data = context_or_dict.user_data if hasattr(context_or_dict, 'user_data') else context_or_dict
        return any(user_data.get(key) for key in StateManager.ALL_WORKFLOW_KEYS)

    @staticmethod
    def get_active_workflows(context_or_dict) -> List[str]:
        """
        Get list of active workflow names.

        Args:
            context_or_dict: Either ContextTypes.DEFAULT_TYPE or a dict (user_data)
        """
        user_data = context_or_dict.user_data if hasattr(context_or_dict, 'user_data') else context_or_dict
        workflows = []
        if any(user_data.get(key) for key in StateManager.EDITOR_KEYS):
            workflows.append('editor')
        if any(user_data.get(key) for key in StateManager.SUDO_KEYS):
            workflows.append('sudo')
        if any(user_data.get(key) for key in StateManager.DESIGN_MGMT_KEYS):
            workflows.append('design_management')
        if any(user_data.get(key) for key in StateManager.SERVER_BILL_KEYS):
            workflows.append('server_bill')
        return workflows
