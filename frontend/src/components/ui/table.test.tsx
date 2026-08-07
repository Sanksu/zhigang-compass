import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from './table'

afterEach(cleanup)

describe('Table', () => {
  it('渲染完整表格结构', () => {
    render(
      <Table>
        <TableCaption>技能清单</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead>技能</TableHead>
            <TableHead>水平</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>Python</TableCell>
            <TableCell>精通</TableCell>
          </TableRow>
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell colSpan={2}>共 1 项</TableCell>
          </TableRow>
        </TableFooter>
      </Table>,
    )
    expect(screen.getByText('技能清单')).toBeInTheDocument()
    expect(screen.getByText('技能')).toBeInTheDocument()
    expect(screen.getByText('Python')).toBeInTheDocument()
    expect(screen.getByText('共 1 项')).toBeInTheDocument()
  })

  it('Table 自定义 className 合并', () => {
    render(<Table className="custom-t">x</Table>)
    expect(screen.getByText('x').className).toContain('custom-t')
  })
})
