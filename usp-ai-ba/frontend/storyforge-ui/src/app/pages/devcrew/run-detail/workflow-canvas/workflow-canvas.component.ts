import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';

import { WorkflowEdge, WorkflowNode } from '../../../../services/devcrew.service';
import { humanizeStatus } from '../../../../services/format-status.util';
import {
  NODE_HEIGHT,
  NODE_WIDTH,
  PositionedNode,
  layoutWorkflow,
} from '../../../../services/workflow-layout.util';

const LOOP_DROP = 40;
const CANVAS_PADDING = 24;

interface FlowEdgePath {
  id: string;
  d: string;
  active: boolean;
}

interface LoopEdgePath {
  id: string;
  d: string;
  label: string | null;
  labelX: number;
  labelY: number;
}

interface CanvasRender {
  positioned: PositionedNode[];
  width: number;
  height: number;
  flowPaths: FlowEdgePath[];
  loopPaths: LoopEdgePath[];
}

@Component({
  selector: 'app-workflow-canvas',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './workflow-canvas.component.html',
  styleUrl: './workflow-canvas.component.css',
})
export class WorkflowCanvasComponent {
  @Input() nodes: WorkflowNode[] = [];
  @Input() edges: WorkflowEdge[] = [];
  @Input() attention: string[] = [];
  @Input() selectedId: string | null = null;
  @Output() select = new EventEmitter<string>();

  readonly nodeWidth = NODE_WIDTH;
  readonly nodeHeight = NODE_HEIGHT;
  readonly canvasPadding = CANVAS_PADDING;
  readonly humanizeStatus = humanizeStatus;

  get render(): CanvasRender {
    const layout = layoutWorkflow(this.nodes, this.edges);
    const byId = new Map(layout.positioned.map((p) => [p.node.id, p]));

    const flowPaths: FlowEdgePath[] = [];
    const loopPaths: LoopEdgePath[] = [];
    for (const edge of this.edges) {
      const from = byId.get(edge.source);
      const to = byId.get(edge.target);
      if (!from || !to) {
        continue;
      }
      if (edge.kind === 'loop') {
        loopPaths.push(this.buildLoopPath(edge, from, to));
      } else {
        flowPaths.push(this.buildFlowPath(edge, from, to));
      }
    }

    return {
      positioned: layout.positioned,
      width: layout.width + CANVAS_PADDING * 2,
      height: layout.height + CANVAS_PADDING * 2,
      flowPaths,
      loopPaths,
    };
  }

  private buildFlowPath(edge: WorkflowEdge, from: PositionedNode, to: PositionedNode): FlowEdgePath {
    const sx = from.x + NODE_WIDTH + CANVAS_PADDING;
    const sy = from.y + NODE_HEIGHT / 2 + CANVAS_PADDING;
    const tx = to.x + CANVAS_PADDING;
    const ty = to.y + NODE_HEIGHT / 2 + CANVAS_PADDING;
    const midX = (sx + tx) / 2;
    return {
      id: edge.id,
      d: `M ${sx},${sy} C ${midX},${sy} ${midX},${ty} ${tx},${ty}`,
      active: edge.active,
    };
  }

  private buildLoopPath(edge: WorkflowEdge, from: PositionedNode, to: PositionedNode): LoopEdgePath {
    const sx = from.x + NODE_WIDTH / 2 + CANVAS_PADDING;
    const sy = from.y + NODE_HEIGHT + CANVAS_PADDING;
    const tx = to.x + NODE_WIDTH / 2 + CANVAS_PADDING;
    const ty = to.y + NODE_HEIGHT + CANVAS_PADDING;
    const dropY = Math.max(sy, ty) + LOOP_DROP;
    return {
      id: edge.id,
      d: `M ${sx},${sy} C ${sx},${dropY} ${tx},${dropY} ${tx},${ty}`,
      label: edge.label,
      labelX: (sx + tx) / 2,
      labelY: dropY,
    };
  }

  onSelect(id: string): void {
    this.select.emit(id);
  }
}
