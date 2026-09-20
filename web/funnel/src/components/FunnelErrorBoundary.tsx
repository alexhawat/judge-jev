import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = {
  children: ReactNode;
  onRetry?: () => void;
};

type State = {
  error: Error | null;
};

/** Keeps toolbar/header alive if the funnel canvas throws. */
export default class FunnelErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[funnel] render error', error, info.componentStack);
  }

  private retry = () => {
    this.setState({ error: null });
    this.props.onRetry?.();
  };

  render() {
    if (this.state.error) {
      return (
        <div className="flow-error" role="alert">
          <p className="flow-error-title">Funnel view hit an error</p>
          <p className="flow-error-detail">{this.state.error.message}</p>
          <button type="button" className="toolbar-btn" onClick={this.retry}>
            Retry funnel
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
